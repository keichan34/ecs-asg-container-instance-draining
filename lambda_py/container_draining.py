"""
Container Draining for ECS instances in an ASG

Based on https://github.com/awslabs/ecs-cid-sample/blob/master/code/index.py
"""

import base64
import json
import time
import random

import boto3
import botocore

def log(msg, level="INFO", **kwargs):
  """
  Prints a simple JSON-formatted log message to stdout.
  """
  print(json.dumps({
    "msg": msg,
    "level": level,
    "asg_draining": kwargs
  }))

# Establish boto3 session
SESSION = boto3.session.Session()
log(f"Session is in region {SESSION.region_name}", level="DEBUG")

EC2_CLIENT = SESSION.client(service_name='ec2')
ECS_CLIENT = SESSION.client(service_name='ecs')
ASG_CLIENT = SESSION.client('autoscaling')
SNS_CLIENT = SESSION.client('sns')

def publish_to_sns(message, topic_arn):
  """
  Publish SNS message to trigger lambda again.

    :param message: To repost the complete original message received when ASG
        terminating event was received.
    :param topic_arn: SNS topic to publish the message to.
  """

  retry_count = message.get('retry_count', 1)
  sleep_time = min(
    (2 ** retry_count) + (random.randint(0, 1000) / 1000),
    30
  )
  log(f"Sleeping for {sleep_time}")
  time.sleep(sleep_time)

  retry_count += 1
  message.update({
    "retry_count": retry_count
  })

  log(f"Publish to SNS topic {topic_arn}", level="DEBUG")
  SNS_CLIENT.publish(
    TopicArn=topic_arn,
    Message=json.dumps(message),
    Subject='Publishing SNS message to invoke lambda again..'
  )
  return "published"

def container_instance_task_status(ec2_instance_id):
  """
  Check task status on the ECS container instance ID.

    :param ec2_instance_id: The EC2 instance ID is used to identify the cluster,
        container instances in cluster
  """
  container_instance_id = None
  tmp_msg_append = None

  cluster_name = get_ecs_cluster_name(ec2_instance_id)

  # Get list of container instance IDs from the cluster_name
  cluster_list_resp = ECS_CLIENT.list_container_instances(cluster=cluster_name)
  container_det_resp = ECS_CLIENT.describe_container_instances(
    cluster=cluster_name,
    containerInstances=cluster_list_resp['containerInstanceArns']
  )

  for container_instances in container_det_resp['containerInstances']:
    if container_instances['ec2InstanceId'] == ec2_instance_id:
      container_instance_id = container_instances['containerInstanceArn']
      tmp_msg_append = {"container_instance_id": container_instance_id}

      # Check if the instance state is set to DRAINING.
      # If not, set it, so the ECS Cluster will handle de-registering instance,
      # draining tasks.
      container_status = container_instances['status']
      if container_status == 'DRAINING':
        log(
          f"Instance currently in draining state.",
          ecs_container_instance_id=container_instance_id,
          ec2_instance_id=ec2_instance_id,
          ecs_cluster_name=cluster_name
        )
      else:
        # Make ECS API call to set the container status to DRAINING
        log(
          f"Setting container instance status to DRAINING",
          ecs_container_instance_id=container_instance_id,
          ec2_instance_id=ec2_instance_id,
          ecs_cluster_name=cluster_name
        )
        ECS_CLIENT.update_container_instances_state(
          cluster=cluster_name,
          containerInstances=[container_instance_id],
          status='DRAINING'
        )

  # Using container Instance ID, get the task list, and task running on that instance.
  if container_instance_id != None:
    # List tasks on the container instance ID, to get task Arns
    list_task_resp = ECS_CLIENT.list_tasks(
      cluster=cluster_name,
      containerInstance=container_instance_id
    )

    # If the chosen instance has tasks
    running_task_count = len(list_task_resp['taskArns'])
    log(
      f"{running_task_count} tasks running",
      running_task_count=running_task_count,
      ec2_instance_id=ec2_instance_id,
      ecs_cluster_name=cluster_name
    )
    if running_task_count > 0:
      return 1, tmp_msg_append

    return 0, tmp_msg_append

  log(
    f"Instance ID: {ec2_instance_id} not in cluster; assuming 0 tasks running."
  )
  return 0, tmp_msg_append

def get_ecs_cluster_name(ec2_instance_id):
  """
  Given the EC2 Instance ID, this function returns the ECS
  Cluster name. Defaults to "default"
  """

  # Get ECS_CLUSTER env var from userdata
  ec2_resp = EC2_CLIENT.describe_instance_attribute(
    InstanceId=ec2_instance_id,
    Attribute='userData'
  )
  userdata_encoded = ec2_resp['UserData'].get('Value', b'')
  userdata_decoded = base64.b64decode(userdata_encoded).decode("utf-8", "ignore")

  cluster_name = "default"

  tmp_list = userdata_decoded.split()
  for token in tmp_list:
    if "ECS_CLUSTER" in token:
      # Split and get the cluster name
      cluster_name = token.split("=")[1]
      log(f"Detected cluster name {cluster_name} from userdata", level="DEBUG")

  return cluster_name

def lambda_handler(event, _context):
  """
  Main Lambda handler
  """

  line = event['Records'][0]['Sns']['Message']
  message = json.loads(line)
  ec2_instance_id = message['EC2InstanceId']
  asg_group_name = message['AutoScalingGroupName']
  sns_arn = event['Records'][0]['EventSubscriptionArn']
  topic_arn = event['Records'][0]['Sns']['TopicArn']

  tmp_msg_append = None

  log(
    "Lambda received event",
    level="DEBUG",
    sns_message=message,
    ec2_instance_id=ec2_instance_id,
    asg_group_name=asg_group_name,
    sns_arn=sns_arn
  )

  # If the event received is instance terminating...
  if 'LifecycleTransition' in list(message.keys()):
    log(f"message autoscaling {message['LifecycleTransition']}")
    if message['LifecycleTransition'].find('autoscaling:EC2_INSTANCE_TERMINATING') > -1:

      # Get lifecycle hook name
      lifecycle_hook_name = message['LifecycleHookName']
      log(f"Setting lifecycle hook name {lifecycle_hook_name}", level="DEBUG")

      log(f"Terminating EC2 Instance...", ec2_instance_id=ec2_instance_id)

      # Check if there are any tasks running on the instance
      tasks_running, tmp_msg_append = container_instance_task_status(ec2_instance_id)
      if tmp_msg_append != None:
        message.update(tmp_msg_append)

      # If tasks are still running...
      if tasks_running == 1:
        log("Tasks still running, republish to SNS...")
        publish_to_sns(message, topic_arn)

      # If tasks are NOT running...
      elif tasks_running == 0:
        log("No tasks running. Completing lifecycle action...")

        try:
          ASG_CLIENT.complete_lifecycle_action(
            LifecycleHookName=lifecycle_hook_name,
            AutoScalingGroupName=asg_group_name,
            LifecycleActionResult='CONTINUE',
            InstanceId=ec2_instance_id)
          log("Completed lifecycle hook action.", ec2_instance_id=ec2_instance_id)
        except botocore.exceptions.ClientError as exc:
          log("error", level="ERROR", exc=str(exc))
