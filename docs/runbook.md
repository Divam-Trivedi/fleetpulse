# Runbook: build, capture evidence, tear down

## Rebuild order
1. `cd infra/aws && terraform init && terraform apply` (creates S3, Firehose, IAM, Snowflake role with placeholder trust).
2. Snowflake worksheet (ACCOUNTADMIN): run `snowflake/01_setup.sql`; copy `STORAGE_AWS_IAM_USER_ARN` and `STORAGE_AWS_EXTERNAL_ID` into `infra/aws/terraform.tfvars`; `terraform apply`.
3. Run `02_ingest.sql`; copy `notification_channel` from `SHOW PIPES` into `terraform.tfvars` as `snowpipe_sqs_arn`; `terraform apply`.
4. Send data: `fleetpulse run -c configs/scenarios/scenario_21.yaml -d 7200 -s firehose --stream-name fleetpulse-telemetry --region us-west-2 --profile fleetpulse` (repeat for seeds 21 to 25 and `configs/predictive.yaml`).
5. Wait a few minutes; run `03_verify.sql`, `04` to `06`, `07_roles.sql`, then `08` to `10`; train with `python -m analytics.train_model`.

## Capture evidence before teardown
Save into `docs/results/`:
- Snowflake: download CSVs from `snowflake/11_export_results.sql` queries (results panel, Download).
- Screenshots: S3 partitioned files, Firehose stream, `SYSTEM$PIPE_STATUS`, `MARTS.ROBOT_STATUS` silent robots, the warning-time result.
- Terminal: save the training output to `docs/results/train_output.txt`; `terraform plan` output.

## Teardown (in this order)
```sql
-- Snowflake, as ACCOUNTADMIN
DROP DATABASE IF EXISTS FLEETPULSE;            -- pipe, stage, tables, dynamic tables, views, ML schema
DROP WAREHOUSE IF EXISTS FLEETPULSE_WH;
DROP RESOURCE MONITOR IF EXISTS FLEETPULSE_RM;
DROP STORAGE INTEGRATION IF EXISTS FLEETPULSE_S3_INT;
DROP ROLE IF EXISTS FLEETPULSE_DEV;
```
```bash
# AWS: empty the bucket first, or destroy fails
aws s3 rm s3://fleetpulse-raw-<ACCOUNT_ID>-us-west-2 --recursive --profile fleetpulse
cd infra/aws && terraform destroy
```
Verify nothing is left:
```bash
aws s3 ls --profile fleetpulse
aws firehose list-delivery-streams --region us-west-2 --profile fleetpulse
aws iam list-roles --profile fleetpulse --query "Roles[?starts_with(RoleName,'fleetpulse')]"
```
Hygiene: deactivate or delete the access keys of the working IAM user, keep the budget alert, and check the billing page the next day.
