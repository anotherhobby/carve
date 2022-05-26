import json
import os
import sys
from carve import load_graph, carve_role_arn, unique_node_values
from aws import *
from deploy_beacons import deployment_list, get_deploy_key
import concurrent.futures


def sf_DescribeDeleteStack(payload):
    account = payload['Account']
    credentials = aws_assume_role(carve_role_arn(account), f"carve-deploy-{payload['Region']}")
    response = aws_describe_stack(
        stackname=payload['StackId'], # deleted stacks require the stack id to be described
        region=payload['Region'],
        credentials=credentials
        )

    payload['StackStatus'] = response['StackStatus']
    return payload


def sf_DeleteStack(payload):
    account = payload['Account']
    region = payload['Region']

    credentials = aws_assume_role(carve_role_arn(account), f"carve-cleanup-{region}")

    # if this is a regional S3 stack, empty the bucket before deleting the stack
    s3_stack = f"{os.environ['Prefix']}carve-managed-bucket-{region}"
    if payload['StackName'] == s3_stack:

        if os.environ['UniqueId'] == "":
            unique = os.environ['OrgId']
        else:
            unique = os.environ['UniqueId']

        bucket = f"{os.environ['Prefix']}carve-managed-bucket-{unique}-{region}"
        aws_purge_s3_bucket(bucket)

    aws_delete_stack(
        stackname=payload['StackName'],
        region=payload['Region'],
        credentials=credentials)

    print(f"WOULD DELETE STACK: {payload['StackName']} from {account} in {region}")

    return payload


def sf_OrganizeDeletions(payload):
    delete_stacks = []
    for task in payload:
        for stack in task['Payload']:
            delete_stacks.append(stack)
    return delete_stacks


def sf_CleanupDeployments(context):
    '''discover all deployments of carve named stacks and determine if they should exist'''

    # check for inactive carve ec2 images and snapshots and clean them up
    cleanup_images()

    deploy_key = get_deploy_key()
    G = load_graph(deploy_key, local=False)

    print(f'cleaning up after graph deploy: {deploy_key}')

    accounts = aws_discover_org_accounts()
    # regions = aws_all_regions()

    # create a list for carve stacks to not delete
    safe_stacks = []

    # # do not delete the s3 stack in the current region
    # deploy_region_list = set(deploy_regions(G))
    # deploy_region_list.add(current_region)

    # for region in aws_all_regions():
    #     s3_stack = f"{os.environ['Prefix']}carve-managed-bucket-{region}"
    #     safe_stacks.append({
    #         'StackName': s3_stack,
    #         'Account': context.invoked_function_arn.split(":")[4],
    #         'Region': region
    #         })

    for stack in deployment_list(G, context):
        safe_stacks.append({
            'StackName': stack['StackName'],
            'Account': stack['Account'],
            'Region': stack['Region']
            })

    # add all private link stacks from the current account for all deploy regions
    for region in sorted(unique_node_values(G, 'Region')):
        safe_stacks.append({
            'StackName': f"{os.environ['Prefix']}carve-managed-privatelink-{region}",
            'Account': aws_current_account(),
            'Region': region
            })

    print(f'all safe stacks: {safe_stacks}')

    # create discovery list of all accounts for step function
    discover_stacks = []
    for account_id, account_name in accounts.items():
        cleanup = {}
        cleanup['Account'] = account_id
        cleanup['SafeStacks'] = []
        for stack in safe_stacks:
            if stack['Account'] == account_id:
                # cleanup['SafeStacks'] = safe_stacks
                cleanup['SafeStacks'].append(stack['StackName'])
        discover_stacks.append(cleanup)

    # returns to a step function iterator
    return discover_stacks


def cleanup_images():
    # get current AMI
    parameter = f"/{os.environ['Prefix']}carve-resources/carve-beacon-ami"

    for region in aws_all_regions():
        print(f'cleaning up images in region {region}')
        try:
            active_image = aws_ssm_get_parameter(parameter, region=region)
            carve_images = aws_describe_all_carve_images(region)
            for image in carve_images['Images']:
                if image['ImageId'] != active_image:
                    print(f"cleaing up {image['ImageId']} in {region}")
                    aws_deregister_image(
                        image['ImageId'],
                        region
                    )
                    aws_delete_snapshot(
                        image['BlockDeviceMappings'][0]['Ebs']['SnapshotId'],
                        region
                    )
        except Exception as e:
            print(f'ERROR cleaning up images in region {region}: {e}')


def sf_DeploymentComplete(payload):
    # not functional yet
    sys.exit()

    # should notify of happiness
    # should move deploy graph to completed
    # need to add a final step to state machine

    # # move deployment object immediately
    # filename = key.split('/')[-1]
    # deploy_key = f"deploy_started/{filename}"
    # aws_copy_s3_object(key, deploy_key, region)
    # aws_delete_s3_object(key, region)


def sf_DiscoverCarveStacks(payload):
    account = payload['Account']
    safe_stacks = payload['SafeStacks']
    credentials = aws_assume_role(carve_role_arn(account), f"carve-cleanup")
    startswith = f"{os.environ['Prefix']}carve-managed-"

    futures = set()
    delete_stacks = []
    with concurrent.futures.ThreadPoolExecutor() as executor:
        for region in aws_all_regions():
            futures.add(executor.submit(
                threaded_find_stacks,
                account=account,
                region=region,
                safe_stacks=safe_stacks,
                startswith=startswith,
                credentials=credentials
            ))
        for future in concurrent.futures.as_completed(futures):
            for stack in future.result():
                delete_stacks.append(stack)

    return delete_stacks


def threaded_find_stacks(account, region, safe_stacks, startswith, credentials):
    # find all carve managed stacks
    stacks = aws_find_stacks(startswith, account, region, credentials)

    if stacks is None:
        # print(f"cannot list stacks in {account} in {region}.")        
        return []        
    elif len(stacks) == 0:
        # print(f"found no stacks to delete in {account} in {region}.")        
        return []
    else:
        delete_stacks = []
        print(f"safe_stacks: {safe_stacks}")
        for stack in stacks:
            if stack['StackName'] not in safe_stacks:
                print(f"found {stack['StackName']} for deletion in {account} in {region}.")
                # create payloads for delete iterator in state machine
                del_stack = {}
                del_stack['StackName'] = stack['StackName']
                del_stack['StackId'] = stack['StackId']
                del_stack['Region'] = region
                del_stack['Account'] = account
                delete_stacks.append(del_stack)
            else:
                print(f"{stack['StackName']} is protected")

        return delete_stacks


def  cleanup_steps_entrypoint(event, context):
    ''' step function tasks for deployment all flow thru here after the lambda_hanlder '''
    try:
        payload = event['Payload']['Input']
    except:
        payload = event['Payload']

    if event['Payload']['CleanupAction'] == 'DescribeDeleteStack':
        # responses come back different after choice state
        if 'Payload' in payload:
            payload = event['Payload']['Input']['Payload']
        response = sf_DescribeDeleteStack(payload)

    elif event['Payload']['CleanupAction'] == 'DeleteStack':
        response = sf_DeleteStack(payload)

    elif event['Payload']['CleanupAction'] == 'CleanupDeployments':
        response = sf_CleanupDeployments(context)

    elif event['Payload']['CleanupAction'] == 'OrganizeDeletions':
        response = sf_OrganizeDeletions(payload)

    elif event['Payload']['CleanupAction'] == 'DiscoverCarveStacks':
        response = sf_DiscoverCarveStacks(payload)

    # return json to step function
    return json.dumps(response, default=str)



