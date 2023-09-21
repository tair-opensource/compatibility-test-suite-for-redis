#!/usr/bin/env python3
import abc
import subprocess
import uuid
from dataclasses import dataclass
from time import sleep
from typing import List, Dict

import redis

import yaml
from alibabacloud_r_kvstore20150101 import models as r_kvstore_20150101_models
from alibabacloud_r_kvstore20150101.client import Client as R_kvstore20150101Client
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_util import models as util_models
from alibabacloud_tea_util.client import Client as UtilClient
from alibabacloud_vpc20160428 import models as vpc_20160428_models
from alibabacloud_vpc20160428.client import Client as Vpc20160428Client


class Instance_info:
    def __init__(self,
                 instance_id="", instance_name="",
                 instance_account="", instance_password="",
                 region_id="",
                 vpc_id="", vsw_id=""):
        self.instance_id = instance_id
        self.instance_name = instance_name
        self.instance_account = instance_account
        self.instance_password = instance_password
        self.region_id = region_id
        self.vpc_id = vpc_id
        self.vsw_id = vsw_id


class CloudRedisCompatabilityTest(abc.ABC):
    def __init__(self, db_configs):
        self.configs = db_configs

    @abc.abstractmethod
    def purchase_redis_instance(self):
        """购买实例"""
        pass

    @abc.abstractmethod
    def configure_redis_instance(self, instance_infos):
        """配置实例"""
        pass

    @abc.abstractmethod
    def run_compatibility_tests(self, instance_infos):
        """运行兼容性测试"""
        pass

    @abc.abstractmethod
    def cleanup_resources(self, instance_infos):
        """清理资源"""
        pass


class AliyunRedisCompatibilityTester(CloudRedisCompatabilityTest):
    def __init__(self, db_configs):
        super().__init__(db_configs)
        self.kv_client = None
        self.runtime = None

    def create_kv_client(self) -> R_kvstore20150101Client:
        """
        使用AK&SK初始化账号Client
        @param: self
        @return: Client
        @throws Exception
        """
        config = open_api_models.Config(
            # 必填，您的 AccessKey ID,
            access_key_id=self.configs['Database']['Tair']['access_key'],
            # 必填，您的 AccessKey Secret,
            access_key_secret=self.configs['Database']['Tair']['access_key_secret']
        )
        # Endpoint 请参考 https://api.aliyun.com/product/R-kvstore
        config.endpoint = f'r-kvstore.aliyuncs.com'
        return R_kvstore20150101Client(config)

    def create_vpc_client(self, region_single) -> Vpc20160428Client:
        config = open_api_models.Config(
            # 必填，您的 AccessKey ID,
            access_key_id=self.configs['Database']['Tair']['access_key'],
            # 必填，您的 AccessKey Secret,
            access_key_secret=self.configs['Database']['Tair']['access_key_secret']
        )
        # Endpoint 请参考 https://api.aliyun.com/product/R-kvstore
        if region_single != "":
            config.endpoint = f'vpc.cn-{region_single}.aliyuncs.com'
        else:
            config.endpoint = f'vpc.aliyuncs.com'
        return Vpc20160428Client(config)

    def choose_region(self):
        describe_regions_request = r_kvstore_20150101_models.DescribeRegionsRequest(
            accept_language='zh-CN'
        )
        try:
            region = self.kv_client.describe_regions_with_options(describe_regions_request, self.runtime)
            region_ids = region.to_map()['body']['RegionIds']['KVStoreRegion']
            local_name = region_ids[0]['RegionId']
            zone_id = region_ids[0]['ZoneIdList']['ZoneId'][0]
            print(f"选择地域为：{local_name}")
            print(f"选择可用区为：{zone_id}")
            return local_name, zone_id
        except Exception as error:
            # 如有需要，请打印 error
            UtilClient.assert_as_string(error.message)

    def create_vpc(self, vpc_region_id, vpc_zone_id):
        """
        创建VPC
        """
        vpc_id = ""
        vsw_id = ""
        create_vpc_request = vpc_20160428_models.CreateVpcRequest(
            region_id=vpc_region_id,
            vpc_name=f"tair_cts_vpc_{str(uuid.uuid4())[:5]}",
            cidr_block='172.16.0.0/24'
        )
        try:
            print("开始创建VPC...")
            # region_single = "hangzhou"
            region_single = vpc_region_id.split('-')[1]
            client = self.create_vpc_client(region_single)
            vpc_info = client.create_vpc_with_options(create_vpc_request, self.runtime)
            sleep(5)
            vpc_id = vpc_info.to_map()['body']['VpcId']
            print(f"创建VPC成功，VPC ID为：{vpc_id}")
        except Exception as error:
            # 如有需要，请打印 error
            UtilClient.assert_as_string(error.message)

        create_vswitch_request = vpc_20160428_models.CreateVSwitchRequest(
            region_id=vpc_region_id,
            vpc_id=vpc_id,
            cidr_block='172.16.0.0/24',
            zone_id=vpc_zone_id,
            v_switch_name=f"tair_cts_vsw_{str(uuid.uuid4())[:5]}",
        )
        try:
            print("开始创建 Vpc 交换机...")
            region_single = vpc_region_id.split('-')[1]
            client = self.create_vpc_client(region_single)
            vsw_info = client.create_vswitch_with_options(create_vswitch_request, self.runtime)
            sleep(5)
            vsw_id = vsw_info.to_map()['body']['VSwitchId']
            print(f"创建 Vpc 交换机成功，Vpc 交换机 ID 为：{vsw_id}")
        except Exception as error:
            UtilClient.assert_as_string(error.message)

        return vpc_id, vsw_id

    def purchase_redis_instance(self):
        """
        阿里云购买Redis实例的实现
        @param: self
        """
        print("开始购买阿里云 Redis 实例...")
        self.kv_client = self.create_kv_client()
        self.runtime = util_models.RuntimeOptions()
        # region_id = "cn-hangzhou" zone_id = "cn-hangzhou-b"
        region_id, zone_id = self.choose_region()
        vpc_id, vsw_id = self.create_vpc(region_id, zone_id)

        # 创建 Tair 实例
        instance_id = ""
        create_tair_instance_request = r_kvstore_20150101_models.CreateTairInstanceRequest(
            region_id=region_id,
            instance_class='tair.rdb.1g',
            zone_id=zone_id,
            vpc_id=vpc_id,
            v_switch_id=vsw_id,
            auto_use_coupon='true',
            charge_type='PostPaid',
            instance_type='tair_rdb',
            auto_pay=True,
        )
        try:
            print("开始创建 Tair 实例...")
            sleep(10)
            tair_info = self.kv_client.create_tair_instance_with_options(create_tair_instance_request,
                                                                         self.runtime).to_map()
            sleep(5)
            instance_id = tair_info['body']['InstanceId']
            if instance_id == "" or instance_id is None:
                print("创建 Tair 实例失败!")
                exit(-1)
            print(f"创建 Tair 实例成功，实例 ID 为：{instance_id}")
        except Exception as error:
            print(error)
            UtilClient.assert_as_string(error.message)

        instance_infos = Instance_info(instance_id=instance_id, region_id=region_id, vpc_id=vpc_id, vsw_id=vsw_id)
        return instance_infos

    def configure_redis_instance(self, instance_infos):
        """阿里云配置Redis实例的实现"""
        print("开始配置阿里云 Tair 实例")
        sleep(5)
        print(f"实例 ID 为：{instance_infos.instance_id}")
        describe_instances_overview_request = r_kvstore_20150101_models.DescribeInstancesOverviewRequest(
            region_id=instance_infos.region_id,
            instance_ids=instance_infos.instance_id,
        )
        # 等待实例创建完成
        try:
            instance_status = ""
            while instance_status != "Normal":
                # 在循环中不断获取最新的instance_desc
                instance_desc = self.kv_client.describe_instances_overview_with_options(
                    describe_instances_overview_request,
                    self.runtime).to_map()
                instance_status = instance_desc['body']['Instances'][0]['InstanceStatus']
                print(f"实例状态为：{instance_status}")
                if instance_status == "Creating":
                    print("实例正在创建中...")
                elif instance_status != "Normal":
                    print("实例创建失败! 状态码:", instance_status)
                    break
                # 等待一段时间再继续检查
                sleep(10)
            print("实例创建结束!")
        except Exception as error:
            # 如有需要，请打印 error
            UtilClient.assert_as_string(error.message)

        # 修改连接权限为开放给所有 IP
        print("开放连接权限...")
        modify_security_ips_request = r_kvstore_20150101_models.ModifySecurityIpsRequest(
            security_ips='0.0.0.0/0',
            instance_id=instance_infos.instance_id,
        )
        try:
            self.kv_client.modify_security_ips_with_options(modify_security_ips_request, self.runtime)
            sleep(60)
        except Exception as error:
            # 如有需要，请打印 error
            UtilClient.assert_as_string(error.message)
        print("连接权限开放完毕")

        # 申请公网连接地址
        print("申请公网连接地址...")
        allocate_instance_public_connection_request = r_kvstore_20150101_models.AllocateInstancePublicConnectionRequest(
            instance_id=instance_infos.instance_id,
            connection_string_prefix='ctstest',
            port='6379'
        )
        try:
            self.kv_client.allocate_instance_public_connection_with_options(allocate_instance_public_connection_request,
                                                                            self.runtime)
            sleep(5)
        except Exception as error:
            # 如有需要，请打印 error
            UtilClient.assert_as_string(error.message)

        # 等待公网连接地址申请完成
        try:
            instance_status = ""
            while instance_status != "Normal":
                # 在循环中不断获取最新的instance_desc
                instance_desc = self.kv_client.describe_instances_overview_with_options(
                    describe_instances_overview_request,
                    self.runtime).to_map()
                instance_status = instance_desc['body']['Instances'][0]['InstanceStatus']
                print(f"实例状态为：{instance_status}")
                if instance_status == "NetworkModifying":
                    print("实例正在修改中...")
                elif instance_status != "Normal":
                    print("实例创建失败! 状态码:", instance_status)
                    break
                # 等待一段时间再继续检查
                sleep(10)
            print("实例公网连接申请完成!")
        except Exception as error:
            # 如有需要，请打印 error
            UtilClient.assert_as_string(error.message)

        # 修改密码
        print("修改密码...")
        reset_account_password_request = r_kvstore_20150101_models.ResetAccountPasswordRequest(
            instance_id=instance_infos.instance_id,
            account_name=instance_infos.instance_id,
            account_password='tair_test'
        )
        try:
            self.kv_client.reset_account_password_with_options(reset_account_password_request, self.runtime)
            sleep(5)
        except Exception as error:
            # 如有需要，请打印 error
            UtilClient.assert_as_string(error.message)

        instance_infos.instance_account = instance_infos.instance_id
        instance_infos.instance_password = 'tair_test'
        print("Tair 配置完毕")

    def run_compatibility_tests(self, instance_infos):
        """阿里云运行兼容性测试的实现"""
        print("开始运行 Tair 兼容性测试")
        print("检测 Tair 实例状态")
        describe_instances_overview_request = r_kvstore_20150101_models.DescribeInstancesOverviewRequest(
            region_id=instance_infos.region_id,
            instance_ids=instance_infos.instance_id,
        )
        try:
            instance_status = ""
            while instance_status != "Normal":
                # 在循环中不断获取最新的instance_desc
                instance_desc = self.kv_client.describe_instances_overview_with_options(
                    describe_instances_overview_request,
                    self.runtime).to_map()
                instance_status = instance_desc['body']['Instances'][0]['InstanceStatus']
                print(f"实例状态为：{instance_status}")
                # 等待一段时间再继续检查
                sleep(10)
            print("开始运行测试!")
        except Exception as error:
            # 如有需要，请打印 error
            UtilClient.assert_as_string(error.message)
        sleep(5)
        command = [
            "python3",  # 使用Python 3
            "redis_compatibility_test.py",
            "--testfile",
            "cts.json",
            "--show-failed",
            "--host",
            "ctstest.redis.rds.aliyuncs.com",
            "--port",
            "6379",
            "--password",
            "tair_test"
        ]
        with open("Ali_test_output.txt", "w") as output_file:
            while True:
                # 通过stdout参数将标准输出重定向到output_file
                result = subprocess.run(command, stdout=output_file, stderr=subprocess.PIPE, text=True)
                if result.returncode == 0:
                    print("测试运行成功")
                    print("运行结果已保存至 Ali_test_output.txt")
                    break  # 如果成功则跳出循环
                else:
                    print("测试运行失败, 准备再来一次")
                    print("标准错误:")
                    print(result.stderr)

    def cleanup_resources(self, instance_infos):
        """阿里云清理资源的实现"""
        sleep(10)
        print("开始释放 Tair 实例...")
        delete_instance_request = r_kvstore_20150101_models.DeleteInstanceRequest(
            instance_id=instance_infos.instance_id
        )
        try:
            self.kv_client.delete_instance_with_options(delete_instance_request, self.runtime)
        except Exception as error:
            UtilClient.assert_as_string(error.message)

        sleep(300)
        print("开始删除 VPC 交换机...")
        region_single = instance_infos.region_id.split('-')[1]
        vpc_client = self.create_vpc_client(region_single)
        delete_vswitch_request = vpc_20160428_models.DeleteVSwitchRequest(
            region_id=instance_infos.region_id,
            v_switch_id=instance_infos.vsw_id,
        )
        try:
            status_code = vpc_client.delete_vswitch_with_options(delete_vswitch_request,
                                                                 self.runtime).to_map()['statusCode']
            print("删除 VPC 交换机结束: statusCode = ", status_code)
        except Exception as error:
            UtilClient.assert_as_string(error.message)

        sleep(10)
        print("开始删除 VPC...")
        delete_vpc_request = vpc_20160428_models.DeleteVpcRequest(
            region_id=instance_infos.region_id,
            vpc_id=instance_infos.vpc_id,
            force_delete=True
        )
        try:
            status_code = vpc_client.delete_vpc_with_options(delete_vpc_request,
                                                             self.runtime).to_map()['statusCode']
            print("删除 VPC 结束: statusCode = ", status_code)
        except Exception as error:
            UtilClient.assert_as_string(error.message)

        print("清理阿里云Redis实例成功")


if __name__ == "__main__":
    print("请选择云服务提供商：")
    print("1. 阿里云")
    print("2. 腾讯云")
    print("3. AWS")
    print("4. Google Cloud")

    choice = input("请输入对应的序号：")

    tester = None

    try:
        with open('config.yaml', 'r') as f:
            configs = yaml.load(f, Loader=yaml.FullLoader)
    except FileNotFoundError as e:
        print(f"error {e}")
        exit(-1)

    if choice == "1":
        tester = AliyunRedisCompatibilityTester(configs)
    elif choice == "2":
        pass
        # TODO: 执行腾讯云相关逻辑
    elif choice == "3":
        pass
        # TODO: 执行AWS相关逻辑
    elif choice == "4":
        pass
        # TODO: 执行Google云相关逻辑
    else:
        print("无效的选择，请输入有效的序号")

    if tester is not None:
        instance_info = None
        try:
            instance_info = tester.purchase_redis_instance()
            tester.configure_redis_instance(instance_info)
            tester.run_compatibility_tests(instance_info)
            tester.cleanup_resources(instance_info)
        except Exception as e:
            print(f"发生错误：{e}")
            if instance_info is not None:
                print("正在清理资源...")
                tester.cleanup_resources(instance_info)
