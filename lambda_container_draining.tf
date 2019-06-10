# See:
# https://aws.amazon.com/blogs/compute/how-to-automate-container-instance-draining-in-amazon-ecs
# https://github.com/awslabs/ecs-cid-sample

variable "autoscaling_group_name" {
    type = "string"
}

resource "aws_autoscaling_lifecycle_hook" "container-draining" {
  name                    = "ecs-default-container-draining"
  autoscaling_group_name  = "${var.autoscaling_group_name}"
  default_result          = "ABANDON"
  heartbeat_timeout       = 900
  lifecycle_transition    = "autoscaling:EC2_INSTANCE_TERMINATING"
  notification_target_arn = "${aws_sns_topic.cluster-instances-asg-lifecycle.arn}"
  role_arn                = "${aws_iam_role.container-draining_asg_lifecycle_hook.arn}"

  depends_on = [
    "aws_iam_role_policy_attachment.container-draining_asg_lifecycle_hook-asn-access",
  ]
}

resource "aws_sns_topic" "cluster-instances-asg-lifecycle" {
  name = "ecs-default-cluster-instances-asg-lifecycle-topic"
}

data "archive_file" "container-draining_zip" {
  type        = "zip"
  output_path = "/tmp/lambda_py/container_draining-${sha256(file("${path.module}/lambda_py/container_draining.py"))}.zip"
  source_file = "${path.module}/lambda_py/container_draining.py"
}

resource "aws_lambda_function" "container-draining_lambda" {
  handler          = "container_draining.lambda_handler"
  function_name    = "ecs-default-container-draining"
  role             = "${aws_iam_role.container-draining_lambda.arn}"
  runtime          = "python3.6"
  filename         = "${data.archive_file.container-draining_zip.output_path}"
  source_code_hash = "${data.archive_file.container-draining_zip.output_base64sha256}"

  timeout = "300"
}

resource "aws_sns_topic_subscription" "cluster-instances-asg-lifecycle-lambda" {
  topic_arn = "${aws_sns_topic.cluster-instances-asg-lifecycle.arn}"
  protocol  = "lambda"
  endpoint  = "${aws_lambda_function.container-draining_lambda.arn}"
}

resource "aws_lambda_alias" "container-draining_lambda" {
  name        = "${aws_lambda_function.container-draining_lambda.function_name}"
  description = "${aws_lambda_function.container-draining_lambda.function_name}"

  function_name    = "${aws_lambda_function.container-draining_lambda.arn}"
  function_version = "$LATEST"
}

resource "aws_lambda_permission" "container-draining_lambda" {
  function_name = "${aws_lambda_function.container-draining_lambda.arn}"

  statement_id = "AllowExecutionFromSNS"
  action       = "lambda:InvokeFunction"
  principal    = "sns.amazonaws.com"

  source_arn = "${aws_sns_topic.cluster-instances-asg-lifecycle.arn}"
}

resource "aws_iam_role" "container-draining_lambda" {
  name               = "ecs-default-container-draining_lambda"
  assume_role_policy = "${file("${path.module}/files/lambda_assume_role.json")}"
}

resource "aws_iam_role_policy_attachment" "container-draining_lambda-basic-exec" {
  role       = "${aws_iam_role.container-draining_lambda.name}"
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "container-draining_lambda-autoscaling-notification" {
  role       = "${aws_iam_role.container-draining_lambda.name}"
  policy_arn = "arn:aws:iam::aws:policy/service-role/AutoScalingNotificationAccessRole"
}

resource "aws_iam_role_policy" "container-draining_lambda" {
  name = "container-draining_lambda"
  role = "${aws_iam_role.container-draining_lambda.name}"

  policy = <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Action": [
        "autoscaling:CompleteLifecycleAction",
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceAttribute",
        "ec2:DescribeInstanceStatus",
        "ec2:DescribeHosts",
        "ecs:ListContainerInstances",
        "ecs:SubmitContainerStateChange",
        "ecs:SubmitTaskStateChange",
        "ecs:DescribeContainerInstances",
        "ecs:UpdateContainerInstancesState",
        "ecs:ListTasks",
        "ecs:DescribeTasks",
        "sns:Publish",
        "sns:ListSubscriptions"
      ],
      "Effect": "Allow",
      "Resource": "*"
    }
  ]
}
EOF
}

resource "aws_iam_role" "container-draining_asg_lifecycle_hook" {
  name               = "ecs-default-container-draining_asg_lifecycle_hook"
  assume_role_policy = "${file("${path.module}/files/autoscaling_assume_role.json")}"
}

resource "aws_iam_role_policy_attachment" "container-draining_asg_lifecycle_hook-asn-access" {
  role       = "${aws_iam_role.container-draining_asg_lifecycle_hook.name}"
  policy_arn = "arn:aws:iam::aws:policy/service-role/AutoScalingNotificationAccessRole"
}
