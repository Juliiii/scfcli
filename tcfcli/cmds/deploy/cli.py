# -*- coding: utf-8 -*-

import os
import sys
import time
import re
import click

from io import BytesIO

import tcfcli.common.base_infor as infor
from tcfcli.help.message import DeployHelp as help
from tcfcli.common.operation_msg import Operation
from tcfcli.common.template import Template
from tcfcli.common.user_exceptions import *
from tcfcli.libs.utils.scf_client import ScfClient
from tcfcli.common import tcsam
from tcfcli.common.user_config import UserConfig
from tcfcli.common.tcsam.tcsam_macro import TcSamMacro as tsmacro
from zipfile import ZipFile, ZIP_DEFLATED
from tcfcli.libs.utils.cos_client import CosClient

_CURRENT_DIR = '.'
_BUILD_DIR = './.tcf_build'
DEF_TMP_FILENAME = 'template.yaml'

REGIONS = infor.REGIONS
SERVICE_RUNTIME = infor.SERVICE_RUNTIME


@click.command(short_help=help.SHORT_HELP)
@click.option('--template-file', '-t', default=DEF_TMP_FILENAME, type=click.Path(exists=True), help=help.TEMPLATE_FILE)
@click.option('--cos-bucket', '-c', type=str, help=help.COS_BUCKET)
@click.option('--name', '-n', type=str, help=help.NAME)
@click.option('--namespace', '-ns', type=str, help=help.NAMESPACE)
@click.option('--region', '-r', type=str, help=help.REGION)
@click.option('--forced', '-f', is_flag=True, default=False, help=help.FORCED)
@click.option('--skip-event', is_flag=True, default=False, help=help.SKIP_EVENT)
@click.option('--without-cos', is_flag=True, default=False, help=help.WITHOUT_COS)
def deploy(template_file, cos_bucket, name, namespace, region, forced, skip_event, without_cos):
    '''
        \b
        Scf cli completes the function package deployment through the deploy subcommand. The scf command line tool deploys the code package, function configuration, and other information specified in the configuration file to the cloud or updates the functions of the cloud according to the specified function template configuration file.
        \b
        The execution of the scf deploy command is based on the function template configuration file. For the description and writing of the specific template file, please refer to the template file description.
            * https://cloud.tencent.com/document/product/583/33454
        \b
        Common usage:
            \b
            * Deploy the package
              $ scf deploy
            \b
            * Package the configuration file, and specify the COS bucket as "temp-code-1253970226"
              $ scf deploy --cos-bucket temp-code-1253970226
    '''

    if region and region not in REGIONS:
        raise ArgsException("The region must in %s." % (", ".join(REGIONS)))
    else:
        package = Package(template_file, cos_bucket, name, region, namespace, without_cos)
        resource = package.do_package()
        if resource == None:
            return
        deploy = Deploy(resource, namespace, region, forced, skip_event)
        deploy.do_deploy()


class Package(object):

    def __init__(self, template_file, cos_bucket, function, region, deploy_namespace, without_cos):
        self.template_file = template_file
        self.template_file_dir = ""
        self.cos_bucket = cos_bucket
        self.check_params()
        template_data = tcsam.tcsam_validate(Template.get_template_data(self.template_file))
        self.resource = template_data.get(tsmacro.Resources, {})
        self.function = function
        self.deploy_namespace = deploy_namespace
        self.region = region
        self.without_cos = without_cos

    def do_package(self):
        for ns in self.resource:
            for func in list(self.resource[ns]):
                if func == tsmacro.Type:
                    continue

                if self.function is not None and func != self.function:
                    self.resource[ns].pop(func)
                    continue

                code_url = self._do_package_core(
                    self.resource[ns][func][tsmacro.Properties].get(tsmacro.CodeUri, ""),
                    ns,
                    func,
                    self.region
                )

                if "cos_bucket_name" in code_url:
                    self.resource[ns][func][tsmacro.Properties]["CosBucketName"] = code_url["cos_bucket_name"]
                    self.resource[ns][func][tsmacro.Properties]["CosObjectName"] = code_url["cos_object_name"]
                    msg = "Upload function zip file '{}' to COS bucket '{}' success.".format(os.path.basename( \
                        code_url["cos_object_name"]), code_url["cos_bucket_name"])
                    Operation(msg).success()
                elif "zip_file" in code_url:
                    #if self.resource[ns][func][tsmacro.Properties][tsmacro.Runtime][0:].lower() in SERVICE_RUNTIME:
                        #error = "Service just support cos to deploy, please set using-cos by 'scf configure set --using-cos y'"
                        #raise UploadFailed(error)
                    self.resource[ns][func][tsmacro.Properties]["LocalZipFile"] = code_url["zip_file"]

        # click.secho("Generate resource '{}' success".format(self.resource), fg="green")
        return self.resource

    def check_params(self):
        if not self.template_file:
            # click.secho("FAM Template Not Found", fg="red")
            raise TemplateNotFoundException("FAM Template Not Found. Missing option --template-file")
        if not os.path.isfile(self.template_file):
            # click.secho("FAM Template Not Found", fg="red")
            raise TemplateNotFoundException("FAM Template Not Found, template-file Not Found")

        self.template_file = os.path.abspath(self.template_file)
        self.template_file_dir = os.path.dirname(os.path.abspath(self.template_file))

        uc = UserConfig()
        if self.cos_bucket and self.cos_bucket.endswith("-" + uc.appid):
            self.cos_bucket = self.cos_bucket.replace("-" + uc.appid, '')

    def file_size_infor(self, size):
        # click.secho(str(size))
        if size >= 20 * 1024 * 1024:
            Operation('Your package is too large and needs to be uploaded via COS.').warning()
            Operation(
                'You can use --cos-bucket BucketName to specify the bucket, or you can use the "scf configure set" to set the default to open the cos upload.').warning()
            raise UploadFailed("Upload faild")
        elif size >= 8 * 1024 * 1024:
            Operation("Package size is over 8M, it is highly recommended that you upload using COS. ").information()
            return

    def _do_package_core(self, func_path, namespace, func_name, region=None):
        zipfile, zip_file_name, zip_file_name_cos = self._zip_func(func_path, namespace, func_name)
        code_url = dict()

        file_size = os.path.getsize(os.path.join(os.getcwd(), _BUILD_DIR, zip_file_name))
        Operation("Package name: %s, package size: %s kb"%(zip_file_name, str(file_size/1000))).process()

        default_bucket_name = ""
        if UserConfig().using_cos.startswith("True"):
            cos_bucket_status = True
            default_bucket_name = "serverless-cloud-function-" + str(UserConfig().appid)
        else:
            cos_bucket_status = False

        if self.without_cos:
            self.file_size_infor(file_size)
            Operation("Uploading this package without COS.").process()
            code_url["zip_file"] = os.path.join(os.getcwd(), _BUILD_DIR, zip_file_name)
            Operation("Upload success").success()

        elif self.cos_bucket:
            bucket_name = self.cos_bucket + "-" + UserConfig().appid
            Operation("Uploading this package to COS, bucket_name: %s" % (bucket_name)).process()
            CosClient(region).upload_file2cos(bucket=self.cos_bucket, file=zipfile.read(), key=zip_file_name_cos)
            Operation("Upload success").success()
            code_url["cos_bucket_name"] = self.cos_bucket
            code_url["cos_object_name"] = "/" + zip_file_name_cos

        elif cos_bucket_status:

            Operation("By default, this package will be uploaded to COS.").information()
            Operation("Default COS-bucket: " + default_bucket_name).information()
            Operation("If you don't want to upload the package to COS by default, you could change your configure!") \
                .information()

            # 根据region设置cos_client
            cos_client = CosClient(region)
            Operation("Checking you COS-bucket.").process()
            # 获取COS bucket
            cos_bucket_status = cos_client.get_bucket(default_bucket_name)

            if cos_bucket_status == -1:
                Operation("reating default COS-bucket: " + default_bucket_name).process()
                create_status = cos_client.create_bucket(bucket=default_bucket_name)
                if create_status == True:
                    cos_bucket_status = 0
                    Operation("Creating success.").success()
                else:
                    try:
                        if "<?xml" in str(create_status):
                            error_code = re.findall("<Code>(.*?)</Code>", str(create_status))[0]
                            error_message = re.findall("<Message>(.*?)</Message>", str(create_status))[0]
                            Operation("COS client error code: %s, message: %s" % (error_code, error_message)).warning()
                    finally:
                        cos_bucket_status = create_status
                        Operation("Creating faild.").warning()

            if cos_bucket_status != 0:

                Operation("There are some exceptions and the process of uploading to COS is terminated!").warning()
                Operation("This package will be uploaded by TencentCloud Cloud API.").information()
                Operation("Uploading this package.").process()
                code_url["zip_file"] = os.path.join(os.getcwd(), _BUILD_DIR, zip_file_name)
                Operation("Upload success").success()

            else:
                # 获取bucket正常，继续流程
                Operation("Uploading to COS, bucket_name:" + default_bucket_name).process()
                cos_client.upload_file2cos(
                    bucket=default_bucket_name,
                    file=zipfile.read(),
                    key=zip_file_name_cos
                )
                code_url["cos_bucket_name"] = default_bucket_name.replace("-" + UserConfig().appid, '') \
                    if default_bucket_name and default_bucket_name.endswith(
                    "-" + UserConfig().appid) else default_bucket_name
                code_url["cos_object_name"] = "/" + zip_file_name_cos

        else:
            Operation( \
                "If you want to increase the upload speed, you can configure using-cos with command：scf configure set") \
                .information()

            self.file_size_infor(file_size)

            Operation("Uploading this package.").process()
            code_url["zip_file"] = os.path.join(os.getcwd(), _BUILD_DIR, zip_file_name)
            Operation("Upload success").success()



        return code_url

    def _zip_func(self, func_path, namespace, func_name):
        buff = BytesIO()
        if not os.path.exists(func_path):
            raise ContextException("Function file or path not found by CodeUri '{}'".format(func_path))

        if self.deploy_namespace and self.deploy_namespace != namespace:
            namespace = self.deploy_namespace

        zip_file_name = str(namespace) + '-' + str(func_name) + '-latest.zip'
        zip_file_name_cos = str(namespace) + '-' + str(func_name) + '-latest' + time.strftime(
            "-%Y-%m-%d-%H-%M-%S", time.localtime(int(time.time()))) + '.zip'
        cwd = os.getcwd()
        os.chdir(self.template_file_dir)
        os.chdir(func_path)

        with ZipFile(buff, mode='w', compression=ZIP_DEFLATED) as zip_object:
            for current_path, sub_folders, files_name in os.walk(_CURRENT_DIR):
                #click.secho(str(current_path))
                if not str(current_path).startswith("./.") and not str(current_path).startswith(r".\."):
                    for file in files_name:
                        zip_object.write(os.path.join(current_path, file))

        os.chdir(cwd)
        buff.seek(0)
        buff.name = zip_file_name

        if not os.path.exists(_BUILD_DIR):
            os.mkdir(_BUILD_DIR)
        zip_file_path = os.path.join(_BUILD_DIR, zip_file_name)

        if os.path.exists(zip_file_path):
            os.remove(zip_file_path)

        # a temporary support for upload func from local zipfile
        with open(zip_file_path, 'wb') as f:
            f.write(buff.read())
            buff.seek(0)
        Operation("Compress function '{}' to zipfile '{}' success".format(zip_file_path, zip_file_name)).success()

        return buff, zip_file_name, zip_file_name_cos


class Deploy(object):
    def __init__(self, resource, namespace, region=None, forced=False, skip_event=False):
        self.resources = resource
        self.namespace = namespace
        self.region = region
        self.forced = forced
        self.skip_event = skip_event

    def do_deploy(self):
        for ns in self.resources:
            if not self.resources[ns]:
                continue
            Operation("Deploy namespace '{ns}' begin".format(ns=ns)).process()
            for func in self.resources[ns]:
                if func == tsmacro.Type:
                    continue
                self._do_deploy_core(self.resources[ns][func], func, ns, self.region,
                                     self.forced, self.skip_event)
            Operation("Deploy namespace '{ns}' end".format(ns=ns)).success()

    def _do_deploy_core(self, func, func_name, func_ns, region, forced, skip_event=False):
        # check namespace exit, create namespace
        if self.namespace and self.namespace != func_ns:
            func_ns = self.namespace

        rep = ScfClient(region).get_ns(func_ns)
        if not rep:
            Operation("{ns} not exists, create it now".format(ns=func_ns)).process()
            err = ScfClient(region).create_ns(func_ns)
            if err is not None:
                if sys.version_info[0] == 3:
                    s = err.get_message()
                else:
                    s = err.get_message().encode("UTF-8")
                raise NamespaceException("Create namespace '{name}' failure. Error: {e}.".format(name=func_ns, e=s))

        err = ScfClient(region).deploy_func(func, func_name, func_ns, forced)
        if err is not None:
            # if sys.version_info[0] == 3:
            s = err.get_message()
            # else:
            #    s = err.get_message().encode("UTF-8")
            if sys.version_info[0] == 2 and isinstance(s, str):
                s = s.encode("utf8")
            err_msg = u"Deploy function '{name}' failure, {e}.".format(name=func_name, e=s)

            if err.get_request_id():
                err_msg += (u"RequestId: {}".format(err.get_request_id()))
            raise CloudAPIException(err_msg)

        Operation("Deploy function '{name}' success".format(name=func_name)).success()
        if not skip_event:
            self._do_deploy_trigger(func, func_name, func_ns, region)

    def _do_deploy_trigger(self, func, func_name, func_ns, region=None):
        proper = func.get(tsmacro.Properties, {})
        events = proper.get(tsmacro.Events, {})
        hasError = None
        for trigger in events:
            err = ScfClient(region).deploy_trigger(events[trigger], trigger, func_name, func_ns)
            if err is not None:
                hasError = err
                if sys.version_info[0] == 3:
                    s = err.get_message()
                else:
                    s = err.get_message().encode("UTF-8")

                Operation("Deploy trigger '{name}' failure. Error: {e}.".format(name=trigger, e=s)).warning()
                if err.get_request_id():
                    click.secho("RequestId: {}".format(err.get_request_id()), fg="red")
                continue
            Operation("Deploy trigger '{name}' success".format(name=trigger)).success()
        if hasError is not None:
            sys.exit(1)
