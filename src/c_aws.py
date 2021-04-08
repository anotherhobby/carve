import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import json
import sys
import os
from multiprocessing import Process, Pipe
import time
import shelve
from boto3.session import Session

current_region = os.environ['AWS_REGION']
boto_config = Config(retries=dict(max_attempts=10))

def aws_assume_role(role_arn, session_name, token_life=900):
    # a function for this lambda to assume a given role
    sts_client = boto3.client(
        'sts',
        region_name=current_region,
        endpoint_url=f'https://sts.{current_region}.amazonaws.com',
        config=boto_config
        )

    try:
        assumed_role_object = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            DurationSeconds=int(token_life))
        return assumed_role_object['Credentials'] 
    except ClientError as e:
        print(f'Failed to assume {role_arn}: {e}')
        sys.exit()


def _aws_assume_role_process(account, role, child_conn):
    '''bakground process to use the aws_assume_role function in parallel'''

    credentials = aws_assume_role(
        role_arn=role.replace(":*:", f":{account}:"), 
        session_name=f"carve_session_{account}"
        )

    response = {"account": account, "credentials": credentials}
    child_conn.send(response)
    child_conn.close()


def aws_parallel_role_creation(accounts, role):
    '''assume roles in every account in parallel, return session creds as dict'''
    print(f'assuming roles in {len(accounts)} accounts')
    a_processes = []
    a_parent_connections = []
    credentials = {}

    for account in accounts:
        a_parent_conn, a_child_conn = Pipe()
        a_parent_connections.append(a_parent_conn)
        a_process = Process(
            target=_aws_assume_role_process,
            args=(account, role, a_child_conn)
            )
        a_processes.append(a_process)
        a_process.start()

    # wait for all processes to finish
    for process in a_processes:
        process.join()

    # add all credentials to a dictionary       
    for parent_connection in a_parent_connections:
        account_creds = parent_connection.recv()
        credentials[account_creds['account']] = account_creds['credentials']

    return credentials


def aws_all_regions():
    # get all regions
    if 'Regions' in os.environ:
        all_regions = os.environ['Regions'].split(",")
        if len(all_regions) == 0:
            all_regions = Session().get_available_regions('cloudformation')        
    else:
        all_regions = Session().get_available_regions('cloudformation')
    # not all regions support what carve does (SNS and other limitations)
    unavailable = ['af-south-1', 'eu-south-1', 'ap-east-1', 'me-south-1']
    regions = []
    for region in all_regions:
        if region not in unavailable:
            regions.append(region)

    # this will disable regions for testing
    return regions


def aws_codepipeline_success(job_id):
    client = boto3.client('codepipeline', region_name=current_region)
    try:
        response = client.put_job_success_result(jobId=job_id)
        return response
    except ClientError as e:
        print(f'error returning success to codepipeline: {e}')


def aws_start_stepfunction(sf_arn, sf_input, name):
    ''' start a step function workflow with the given input '''

    client = boto3.client('stepfunctions', region_name=current_region)
    sm_input = json.dumps(sf_input)

    response = client.start_execution(
        stateMachineArn=sf_arn,
        name=name,
        input=sm_input)

    return response


def aws_describe_stack(stackname, region, credentials):
    ''' return a stack description if it exists ''' 
    client = boto3.client(
        'cloudformation',
        config=boto_config,
        region_name=region,
        aws_access_key_id = credentials['AccessKeyId'],
        aws_secret_access_key = credentials['SecretAccessKey'],
        aws_session_token = credentials['SessionToken']
        )
    try:
        stack = client.describe_stacks(StackName=stackname)['Stacks'][0]
    except ClientError as e:
        stack = None

    return stack


# def aws_put_asm_policy():
#     client = boto3.client('secretsmanager', config=boto_config)



def aws_get_carve_tags(lambda_arn):
    ''' get my own tags and format for CFN calls '''

    # check for cached tags to save API calls
    cfn_tags = shelve.open('/tmp/tags_cache', writeback=True)

    # if not cached, get tags
    if len(cfn_tags) == 0:
        client = boto3.client('lambda')
        response = client.list_tags(Resource=lambda_arn)

        cfn_tags = []
        for key, value in response['Tags'].items():
            if key.startswith("aws:"):
                pass
            else:
                tag = {}
                tag['Key'] = key
                tag['Value'] = value
                cfn_tags.append(tag)
    else:
        print(f"found cached tags: {cfn_tags}")

    return cfn_tags


def aws_get_orgid():
    client = boto3.client('organizations', config=boto_config)
    response = client.describe_organization()
    return(response['Organization']['MasterAccountId'])


def aws_execute_change_set(changesetname, stackname, region, credentials):
    client = boto3.client(
        'cloudformation',
        config=boto_config,
        region_name=region,
        aws_access_key_id = credentials['AccessKeyId'],
        aws_secret_access_key = credentials['SecretAccessKey'],
        aws_session_token = credentials['SessionToken']
        )

    response = client.execute_change_set(
        ChangeSetName=changesetname,
        StackName=stackname)
    return response


def aws_describe_transit_gateways(region, credentials):
    client = boto3.client(
        'ec2',
        config=boto_config,
        region_name=region,
        aws_access_key_id = credentials['AccessKeyId'],
        aws_secret_access_key = credentials['SecretAccessKey'],
        aws_session_token = credentials['SessionToken']
    )
    paginator = client.get_paginator('describe_transit_gateways')
    results = []
    for page in paginator.paginate():
        for each in page['TransitGateways']:
            results.append(each)
    return results


def aws_describe_transit_gateway_attachments(region, credentials):
    client = boto3.client(
        'ec2',
        config=boto_config,
        region_name=region,
        aws_access_key_id = credentials['AccessKeyId'],
        aws_secret_access_key = credentials['SecretAccessKey'],
        aws_session_token = credentials['SessionToken']
    )
    paginator = client.get_paginator('describe_transit_gateway_attachments')
    results = []
    for page in paginator.paginate():
        for each in page['TransitGatewayAttachments']:
            results.append(each)
    return results


def aws_describe_transit_gateway_vpc_attachments(region, credentials):
    client = boto3.client(
        'ec2',
        config=boto_config,
        region_name=region,
        aws_access_key_id = credentials['AccessKeyId'],
        aws_secret_access_key = credentials['SecretAccessKey'],
        aws_session_token = credentials['SessionToken']
    )
    paginator = client.get_paginator('describe_transit_gateway_vpc_attachments')
    results = []
    for page in paginator.paginate():
        for each in page['TransitGatewayVpcAttachments']:
            results.append(each)
    return results


def aws_describe_transit_gateway_route_tables(region, credentials):
    client = boto3.client(
        'ec2',
        config=boto_config,
        region_name=region,
        aws_access_key_id = credentials['AccessKeyId'],
        aws_secret_access_key = credentials['SecretAccessKey'],
        aws_session_token = credentials['SessionToken']
    )
    paginator = client.get_paginator('describe_transit_gateway_route_tables')
    results = []
    for page in paginator.paginate():
        for each in page['TransitGatewayRouteTables']:
            results.append(each)
    return results


# def aws_describe_transit_gateway_peering_attachments(tgw_id, region, credentials):
#     client = boto3.client(
#         'ec2',
#         config=boto_config,
#         region_name=region,
#         aws_access_key_id = credentials['AccessKeyId'],
#         aws_secret_access_key = credentials['SecretAccessKey'],
#         aws_session_token = credentials['SessionToken']
#     )
#     paginator = client.get_paginator('describe_transit_gateway_peering_attachments')
#     ta = []

#     for page in paginator.paginate(TransitGatewayAttachmentIds=[tgw_id]):
#         for t in page['TransitGatewayAttachmentIds']:
#             ta.append(t)
#     return ta


# def aws_describe_transit_gateway_vpc_attachments(tgw_id, region, credentials):
#     client = boto3.client(
#         'ec2',
#         config=boto_config,
#         region_name=region,
#         aws_access_key_id = credentials['AccessKeyId'],
#         aws_secret_access_key = credentials['SecretAccessKey'],
#         aws_session_token = credentials['SessionToken']
#     )
# paginator = client.get_paginator('describe_transit_gateway_attachments')
# ta = []

# for page in paginator.paginate(TransitGatewayAttachmentIds=[tgw_id]):
#     for t in page['TransitGatewayAttachmentIds']:
#         ta.append(t)
# return ta


def aws_create_stack(stackname, region, template, parameters, credentials, tags):

    client = boto3.client(
        'cloudformation',
        config=boto_config,
        region_name=region,
        aws_access_key_id = credentials['AccessKeyId'],
        aws_secret_access_key = credentials['SecretAccessKey'],
        aws_session_token = credentials['SessionToken']
        )

    response = client.create_stack(
        StackName=stackname,
        TemplateBody=template,
        Parameters=parameters,
        Capabilities=['CAPABILITY_NAMED_IAM'],
        Tags=tags
        )

    return response


def aws_delete_stack(stackname, region, credentials):

    client = boto3.client(
        'cloudformation',
        config=boto_config,
        region_name=region,
        aws_access_key_id = credentials['AccessKeyId'],
        aws_secret_access_key = credentials['SecretAccessKey'],
        aws_session_token = credentials['SessionToken']
        )

    response = client.delete_stack(StackName=stackname)

    return response


def aws_create_changeset(stackname, changeset_name, region, template, parameters, credentials, tags):
    '''deploy SAM template thru changesets'''

    client = boto3.client(
        'cloudformation',
        config=boto_config,
        region_name=region,
        aws_access_key_id = credentials['AccessKeyId'],
        aws_secret_access_key = credentials['SecretAccessKey'],
        aws_session_token = credentials['SessionToken']
        )

    response = client.create_change_set(
        StackName=stackname,
        ChangeSetName=changeset_name,
        TemplateBody=template,
        Tags=tags,
        Parameters=parameters,
        Capabilities=['CAPABILITY_NAMED_IAM']
        )

    # returns...
    # {
    #     'Id': 'string',
    #     'StackId': 'string'
    # }

    return response


def aws_describe_change_set(changesetname, region, credentials):
    client = boto3.client(
        'cloudformation',
        config=boto_config,
        region_name=region,
        aws_access_key_id = credentials['AccessKeyId'],
        aws_secret_access_key = credentials['SecretAccessKey'],
        aws_session_token = credentials['SessionToken']
        )

    response = client.describe_change_set(ChangeSetName=changesetname)
    return response


def aws_find_stacks(startswith, region, credentials):
    client = boto3.client(
        'cloudformation',
        config=boto_config,
        region_name=region,
        aws_access_key_id = credentials['AccessKeyId'],
        aws_secret_access_key = credentials['SecretAccessKey'],
        aws_session_token = credentials['SessionToken']
        )
    paginator = client.get_paginator('list_stacks')
    stacks = []
    for page in paginator.paginate():
        for stack in page['StackSummaries']:
            if stack['StackName'].startswith(startswith):
                stacks.append(stack)
    return stacks


def aws_newest_s3(path, bucket=os.environ['CarveS3Bucket']):
    # return the newest file in an S3 path
    client = boto3.client('s3', config=boto_config)
    objs = client.list_objects_v2(Bucket=bucket, Prefix=path)
    if objs['KeyCount'] > 0:
        contents = objs['Contents']
        get_last_modified = lambda obj: int(obj['LastModified'].strftime('%s'))
        newest = [obj['Key'] for obj in sorted(contents, key=get_last_modified)][0]
        return newest
    else:
        return None


def aws_read_s3_direct(key, region):
    # get graph from S3
    resource = boto3.resource('s3', config=boto_config)
    try:
        obj = resource.Object(os.environ['CarveS3Bucket'], key)
        return obj.get()['Body'].read().decode('utf-8')
    except ClientError as e:
        print(f"error reading s3: {e}")
        return None


def aws_copy_s3_object(key, target_key, source_bucket=os.environ['CarveS3Bucket'], target_bucket=os.environ['CarveS3Bucket']):
    resource = boto3.resource('s3', config=boto_config)
    src = {
        "Bucket": source_bucket,
        "Key": key
    }
    bucket = resource.Bucket(target_bucket)
    response = bucket.copy(src, target_key)
    return response

def aws_delete_s3_object(key, region):
    # get graph from S3
    resource = boto3.resource('s3', config=boto_config)
    response = resource.Object(os.environ['CarveS3Bucket'], key).delete()
    return response

def aws_get_carve_s3(key, file_path, bucket=None):
    '''
    writes file_path to the carve s3 bucket
    '''
    client = boto3.client('s3', config=boto_config)
    if bucket is None:
        bucket = os.environ['CarveS3Bucket']
    try:
        response = client.download_file(Bucket=bucket, Key=key, Filename=file_path)
        return response
    except ClientError as e:
        print(f's3 error: {e}')
        # logger.exception(f'Failed to write outputs/logs s3 bucket')



def aws_states_list_executions(arn):
    client = boto3.client('stepfunctions', config=boto_config)
    response = client.list_executions(stateMachineArn=arn)
    return response['executions']


def aws_create_s3_path(path):
    if path.endswith("/"):
        s3path = path
    else:
        s3path = f'{path}/'

    client = boto3.client('s3', config=boto_config)
    try:
        client.put_object(
            Bucket=os.environ['CarveS3Bucket'],
            Key=s3path,
            ACL='bucket-owner-full-control'
            )
    except ClientError as e:
        print(f'error creating s3 path: {e}')


def aws_delete_bucket_notification():
    client = boto3.client('s3', config=boto_config)
    try:
        response = client.put_bucket_notification_configuration(
          Bucket=os.environ['CarveS3Bucket'],
          NotificationConfiguration={}
        )
        return response
    except ClientError as e:
        print(f'error creating bucket notification: {e}')


# def aws_empty_bucket():
#     bucket = os.environ['CarveS3Bucket']
#     client = boto3.client('s3', config=boto_config)
#     paginator = client.get_paginator('list_object_versions')

#     delete_list = []
#     for response in paginator.paginate(Bucket=bucket):
#         if 'DeleteMarkers' in response:
#             for mark in response['DeleteMarkers']:
#                 delete_list.append({'Key': mark['Key'], 'VersionId': mark['VersionId']})

#         if 'Versions' in response:
#             for version in response['Versions']:
#                 delete_list.append({'Key': version['Key'], 'VersionId': version['VersionId']})

#     for i in range(0, len(delete_list), 1000):
#         response = client.delete_objects(
#             Bucket=bucket,
#             Delete={
#                 'Objects': delete_list[i:i+1000],
#                 'Quiet': True
#             }
#         )
#         print(f"purged s3 bucket: {bucket}")

def aws_describe_peers(region, credentials):
    client = boto3.client(
        'ec2',
        config=boto_config,
        region_name=region,
        aws_access_key_id = credentials['AccessKeyId'],
        aws_secret_access_key = credentials['SecretAccessKey'],
        aws_session_token = credentials['SessionToken']
        )

    paginator = client.get_paginator('describe_vpc_peering_connections')
    pcxs = []
    for page in paginator.paginate():
        for pcx in page['VpcPeeringConnections']:
            pcxs.append(pcx)
    return pcxs


def aws_describe_subnets(region, credentials, account_id):
    client = boto3.client(
        'ec2',
        config=boto_config,
        region_name=region,
        aws_access_key_id = credentials['AccessKeyId'],
        aws_secret_access_key = credentials['SecretAccessKey'],
        aws_session_token = credentials['SessionToken']
        )
    try:
        paginator = client.get_paginator('describe_subnets')
        subnets = []
        for page in paginator.paginate():
            for subnet in page['Subnets']:
                subnets.append(subnet)
        return subnets
    except ClientError as e:
        print(f"error descibing subnets in {region} in {account_id}: {e}")
        return []

def aws_describe_vpcs(region, credentials):
    client = boto3.client(
        'ec2',
        config=boto_config,
        region_name=region,
        aws_access_key_id = credentials['AccessKeyId'],
        aws_secret_access_key = credentials['SecretAccessKey'],
        aws_session_token = credentials['SessionToken']
        )

    paginator = client.get_paginator('describe_vpcs')
    vpcs = []
    for page in paginator.paginate():
        for vpc in page['Vpcs']:
            vpcs.append(vpc)
    return vpcs


def aws_purge_s3_bucket(bucket=os.environ['CarveS3Bucket']):
    client = boto3.resource('s3', config=boto_config)
    print(f"purging bucket: {bucket}") 
    bucket = client.Bucket(bucket)
    try:
        bucket.objects.all().delete()
    except ClientError as e:
        print(f'error purging bucket {bucket}: {e}')

def aws_purge_s3_path(path):
    client = boto3.resource('s3', config=boto_config)
    bucket = client.Bucket(os.environ['CarveS3Bucket'])
    bucket.objects.filter(Prefix=path).delete()


def aws_list_s3_path(path, max_keys=1):
    client = boto3.client("s3")
    response = client.list_objects_v2(
            Bucket=os.environ['CarveS3Bucket'],
            Prefix=path,
            MaxKeys=max_keys)
    return response

def aws_put_bucket_policy(bucket, function_arn):
    client = boto3.client('s3', config=boto_config)
    try:
        response = client.put_bucket_policy(
            Bucket=os.environ['CarveS3Bucket'],
            Policy='policy'
        )
        return response
    except ClientError as e:
        print(f'error putting bucket policy: {e}')



def aws_get_bucket_policy(bucket):
    s3_resource = boto3.resource('s3', config=boto_config)
    try:
        policy = s3_resource.BucketPolicy(bucket)
        return policy
    except ClientError as e:
        print(f'error getting bucket policy for {bucket}: {e}')


def aws_put_bucket_notification(path, function_arn, notification_id="CarveDeploy"):
    client = boto3.client('s3', config=boto_config)
    try:
        response = client.put_bucket_notification_configuration(
          Bucket=os.environ['CarveS3Bucket'],
          NotificationConfiguration={
            'LambdaFunctionConfigurations': [
              {
                'Id': notification_id,
                'LambdaFunctionArn': function_arn,
                'Events': [
                  's3:ObjectCreated:*'
                ],
                'Filter': {
                  'Key': {
                    'FilterRules': [
                      {
                        'Name': 'prefix',
                        'Value': path
                      },
                      {
                        'Name': 'suffix',
                        'Value': '.json'
                      }
                    ]
                  }
                }
              }
            ]
          }
        )
        return response
    except ClientError as e:
        print(f'error creating bucket notification: {e}')


def aws_upload_file_carve_s3(key, file_path):
    '''
    writes file_path to the carve s3 bucket
    '''
    client = boto3.client('s3', config=boto_config)

    try:
        # print(f"bucket = {os.environ['CarveS3Bucket']}")
        # print(f"file_path = {file_path}")
        # print(f"key = {key}")
        response = client.upload_file(
            Filename=file_path,
            Bucket=os.environ['CarveS3Bucket'],
            Key=key,
            ExtraArgs={'ACL': 'bucket-owner-full-control'}
            )
        return response
    except ClientError as e:
        print(f's3 error: {e}')
        # logger.exception(f'Failed to write outputs/logs s3 bucket')



# if __name__ == '__main__':
#     main()