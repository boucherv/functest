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
import yaml
import os_client_config
import shade

import six
import pkg_resources
from xtesting.energy import energy
from xtesting.core import testcase

from functest.core import singlevm
from functest.opnfv_tests.vnf.ims import clearwater_ims_base
from functest.utils import config

__author__ = "Valentin Boucher <valentin.boucher@kontron.com>"


class HeatIms(singlevm.SingleVm2):
    """Clearwater vIMS deployed with Heat Orchestrator Case."""

    __logger = logging.getLogger(__name__)

    filename_alt = ('/home/opnfv/functest/images/'
                    'ubuntu-14.04-server-cloudimg-amd64-disk1.img')

    flavor_alt_ram = 2048
    flavor_alt_vcpus = 2
    flavor_alt_disk = 25

    quota_security_group = 20
    quota_security_group_rule = 100
    quota_port = 50

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

        self.case_dir = pkg_resources.resource_filename(
            'functest', 'opnfv_tests/vnf/ims')
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

        self.image_alt = None
        self.flavor_alt = None

    def execute(self):
        # pylint: disable=too-many-locals,too-many-statements
        """
        Prepare Tenant/User

        network, security group, fip, VM creation
        """
        start_time = time.time()
        self.orig_cloud.set_network_quotas(
            self.project.project.name,
            security_group=self.quota_security_group,
            security_group_rule=self.quota_security_group_rule,
            port=self.quota_port)
        duration = time.time() - start_time

        if (self.deploy_vnf() or self.test_vnf()):
            self.result = 100
            return 0
        self.result = 1/3 * 100
        return 1

    def run(self, **kwargs):
        """Boot the new VM

        Here are the main actions:
        - add a new ssh key
        - boot the VM
        - create the security group
        - execute the right command over ssh

        Returns:
        - TestCase.EX_OK
        - TestCase.EX_RUN_ERROR on error
        """
        status = testcase.TestCase.EX_RUN_ERROR
        try:
            assert self.cloud
            self.result = 0
            if not self.execute():
                self.result = 100
                status = testcase.TestCase.EX_OK
        except Exception:  # pylint: disable=broad-except
            self.__logger.exception('Cannot run %s', self.case_name)
        finally:
            self.stop_time = time.time()
        return status

    def deploy_vnf(self):
        """Deploy Clearwater IMS."""
        start_time = time.time()

        super(HeatIms, self).prepare()

        self.image_alt = self.publish_image_alt()
        self.flavor_alt = self.create_flavor_alt()
        # KeyPair + Image + Flavor OK

        self.__logger.info("Upload VNFD")

        descriptor = self.vnf['descriptor']

        envfile_path = os.path.join(self.case_dir, descriptor.get('envfile'))
        with open(envfile_path) as envfile:
            envfile_content = yaml.safe_load(envfile)
        envfile.close()

        envfile_content['parameters']['public_mgmt_net_id'] = self.ext_net.id
        envfile_content['parameters']['public_sig_net_id'] = self.ext_net.id
        envfile_content['parameters']['flavor'] = self.flavor_alt.name
        envfile_content['parameters']['image'] = self.image_alt.name
        envfile_content['parameters']['key_name'] = self.keypair.name

        with open(envfile_path, 'w') as envfile:
            yaml.dump(envfile_content, envfile, default_flow_style=False)
        envfile.close()

        self.__logger.info("Create VNF Instance")
        self.stack = self.cloud.create_stack(name=descriptor.get('name'),
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

        testing = clearwater_ims_base.ClearwaterOnBoardingBase(self.case_name)
        outputs = self.cfy_client.deployments.outputs.get(
            self.vnf['descriptor'].get('name'))['outputs']
        dns_ip = outputs['dns_ip']
        ellis_ip = outputs['ellis_ip']
        testing.config_ellis(ellis_ip)

        if not dns_ip:
            return False

        short_result = testing.run_clearwater_live_test(
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
