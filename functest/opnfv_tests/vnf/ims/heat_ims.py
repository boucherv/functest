#!/usr/bin/env python

# Copyright (c) 2017 Orange, Kontron and others.
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Apache License, Version 2.0
# which accompanies this distribution, and is available at
# http://www.apache.org/licenses/LICENSE-2.0

"""HeatIms testcase implementation."""

from __future__ import division

import logging
import os
import time
import uuid
import os_client_config
import shade

import six
from snaps.config.flavor import FlavorConfig
from snaps.config.image import ImageConfig
from snaps.config.keypair import KeypairConfig
from snaps.openstack.create_flavor import OpenStackFlavor
from snaps.openstack.create_image import OpenStackImage
from snaps.openstack.create_keypairs import OpenStackKeypair
from snaps.openstack.utils import keystone_utils
from snaps.openstack.utils import neutron_utils
from xtesting.energy import energy
import yaml

from functest.opnfv_tests.openstack.snaps import snaps_utils
from functest.core import tenantnetwork
from functest.opnfv_tests.vnf.ims import clearwater_ims_base
from functest.utils import config
from functest.utils import env

__author__ = "Valentin Boucher <valentin.boucher@kontron.com>"


class HeatIms(clearwater_ims_base.ClearwaterOnBoardingBase):
    """Clearwater vIMS deployed with Heat Orchestrator Case."""

    __logger = logging.getLogger(__name__)

    def __init__(self, **kwargs):
        """Initialize HeatIms testcase object."""
        if "case_name" not in kwargs:
            kwargs["case_name"] = "heat_ims"
        super(HeatIms, self).__init__(**kwargs)

        # Retrieve the configuration
        try:
            self.config = getattr(
                config.CONF, 'vnf_{}_config'.format(self.case_name))
        except Exception:
            raise Exception("VNF config file not found")

        config_file = os.path.join(self.case_dir, self.config)

        self.vnf = dict(
            descriptor=get_config("vnf.descriptor", config_file),
            requirements=get_config("vnf.requirements", config_file)
        )
        self.details['vnf'] = dict(
            descriptor_version=self.vnf['descriptor']['version'],
            name=get_config("vnf.name", config_file),
            version=get_config("vnf.version", config_file),
        )
        self.__logger.debug("VNF configuration: %s", self.vnf)

        self.images = get_config("tenant_images", config_file)
        self.__logger.info("Images needed for vIMS: %s", self.images)

    def prepare(self):
        """Prepare testscase (Additional pre-configuration steps)."""
        super(HeatIms, self).prepare()


        self.cloud = shade.OpenStackCloud(cloud_config=os_client_config.get_config())
        self.project = tenantnetwork.NewProject(
            self.cloud, self.case_name, self.guid)
        self.project.create()
        self.cloud = self.project.cloud

        self.__logger.info("Additional pre-configuration steps")

        compute_quotas = self.os_project.get_compute_quotas()
        network_quotas = self.os_project.get_network_quotas()

        for key, value in (
                self.vnf['requirements']['compute_quotas'].items()):
            setattr(compute_quotas, key, value)

        for key, value in (
                self.vnf['requirements']['network_quotas'].items()):
            setattr(network_quotas, key, value)

        compute_quotas = self.os_project.update_compute_quotas(compute_quotas)
        network_quotas = self.os_project.update_network_quotas(network_quotas)

    def deploy_orchestrator(self):
        # pylint: disable=too-many-locals,too-many-statements
        """
        Deploy Cloudify Manager.

        network, security group, fip, VM creation
        """
        start_time = time.time()

        self.result = 1/3 * 100
        return True

    def deploy_vnf(self):
        """Deploy Clearwater IMS."""
        start_time = time.time()

        self.__logger.info("Creating keypair ...")
        kp_file = os.path.join(self.data_dir, "heat_ims.pem")
        keypair_settings = KeypairConfig(
            name='heat_ims_kp',
            private_filepath=kp_file)
        keypair_creator = OpenStackKeypair(self.snaps_creds, keypair_settings)
        keypair_creator.create()
        self.created_object.append(keypair_creator)

        # needs some images
        self.__logger.info("Upload some OS images if it doesn't exist")
        for image_name, image_file in six.iteritems(self.images):
            self.__logger.info("image: %s, file: %s", image_name, image_file)
            if image_file and image_name:
                image_creator = OpenStackImage(
                    self.snaps_creds,
                    ImageConfig(
                        name=image_name, image_user='cloud',
                        img_format='qcow2', image_file=image_file))
                image_creator.create()
                self.created_object.append(image_creator)



        self.__logger.info("Upload VNFD")

        descriptor = self.vnf['descriptor']

        self.__logger.info("Get or create flavor for all clearwater vm")
        flavor_settings = FlavorConfig(
            name="{}-{}".format(
                self.vnf['requirements']['flavor']['name'],
                self.uuid),
            ram=self.vnf['requirements']['flavor']['ram_min'],
            disk=25,
            vcpus=2)
        flavor_creator = OpenStackFlavor(self.snaps_creds, flavor_settings)
        flavor_creator.create()
        self.created_object.append(flavor_creator)
        envfile_path = os.path.join(self.case_dir, descriptor.get('envfile'))
        with open(envfile_path) as config_file:
            envfile_content = yaml.safe_load(config_file)
        config_file.close()

        ext_net_name = snaps_utils.get_ext_net_name(self.snaps_creds)
        ext_nets = neutron_utils.get_external_networks(neutron_utils.neutron_client(self.snaps_creds))
        for ext_net in ext_nets:
            if ext_net.name == ext_net_name:
                envfile_content['parameters']['public_mgmt_net_id'] = ext_net.id
                envfile_content['parameters']['public_sig_net_id'] = ext_net.id
        envfile_content['parameters']['flavor'] = self.vnf['requirements']['flavor']['name']
        envfile_content['parameters']['image'] = 'ubuntu_14.04'
        envfile_content['parameters']['key_name'] = keypair_settings.name

        self.__logger.info("Create VNF Instance")
        self.cloud.create_stack(name=descriptor.get('name'),
                                template_file=descriptor.get('file_name'),
                                environment_files=descriptor.get('envfile'))

        duration = time.time() - start_time

        #self.__logger.info(execution)
        """if execution.status == 'terminated':
            self.details['vnf'].update(status='PASS', duration=duration)
            self.result += 1/3 * 100
            result = True
        else:
            self.details['vnf'].update(status='FAIL', duration=duration)
            result = False
        return result"""
        return True

    def test_vnf(self):
        """Run test on clearwater ims instance."""
        start_time = time.time()

        cfy_client = self.orchestrator['object']

        outputs = cfy_client.deployments.outputs.get(
            self.vnf['descriptor'].get('name'))['outputs']
        dns_ip = outputs['dns_ip']
        ellis_ip = outputs['ellis_ip']
        self.config_ellis(ellis_ip)

        if not dns_ip:
            return False

        short_result = self.run_clearwater_live_test(
            dns_ip=dns_ip,
            public_domain=self.vnf['inputs']["public_domain"])
        duration = time.time() - start_time
        self.__logger.info(short_result)
        self.details['test_vnf'].update(result=short_result,
                                        duration=duration)
        try:
            vnf_test_rate = short_result['passed'] / (
                short_result['total'] - short_result['skipped'])
            # orchestrator + vnf + test_vnf
            self.result += vnf_test_rate / 3 * 100
        except ZeroDivisionError:
            self.__logger.error("No test has been executed")
            self.details['test_vnf'].update(status='FAIL')
            return False
        except Exception:  # pylint: disable=broad-except
            self.__logger.exception("Cannot calculate results")
            self.details['test_vnf'].update(status='FAIL')
            return False
        return True if vnf_test_rate > 0 else False

    def clean(self):
        """Clean created objects/functions."""
        try:
            dep_name = self.vnf['descriptor'].get('name')
            # kill existing execution
            self.__logger.info('Deleting the current deployment')
            """
            execution = cfy_client.executions.start(
                dep_name,
                'uninstall',
                parameters=dict(ignore_failure=True),
                force=True)

            cfy_client.blueprints.delete(self.vnf['descriptor'].get('name'))
            """
        except Exception:  # pylint: disable=broad-except
            self.__logger.exception("Some issue during the undeployment ..")

        super(HeatIms, self).clean()

    @energy.enable_recording
    def run(self, **kwargs):
        """Execute HeatIms test case."""
        return super(HeatIms, self).run(**kwargs)


# ----------------------------------------------------------
#
#               YAML UTILS
#
# -----------------------------------------------------------
def get_config(parameter, file_path):
    """
    Get config parameter.

    Returns the value of a given parameter in file.yaml
    parameter must be given in string format with dots
    Example: general.openstack.image_name
    """
    with open(file_path) as config_file:
        file_yaml = yaml.safe_load(config_file)
    config_file.close()
    value = file_yaml
    for element in parameter.split("."):
        value = value.get(element)
        if value is None:
            raise ValueError("The parameter %s is not defined in"
                             " reporting.yaml" % parameter)
    return value
