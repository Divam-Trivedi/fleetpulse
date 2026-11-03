# Runbook: build, capture evidence, tear down

## Rebuild order
1. `cd infra/aws && terraform init && terraform apply` (S3, Firehose JSON and Parquet streams, Glue, IAM, Snowflake role with placeholder trust).
2. Snowflake worksheet (ACCOUNTADMIN): run `snowflake/01_setup.sql`; copy `STORAGE_AWS_IAM_USER_ARN` and `STORAGE_AWS_EXTERNAL_ID` into `infra/aws/terraform.tfvars`; `terraform apply`.
3. Run `02_ingest.sql`; copy `notification_channel` from `SHOW PIPES` into `terraform.tfvars` as `snowpipe_sqs_arn`; `terraform apply`.
4. Parquet path: run `13_parquet_ingest.sql`; if its pipe's `notification_channel` matches, set `snowpipe_parquet_enabled = true` (otherwise also `snowpipe_sqs_arn_parquet`); `terraform apply`.
5. Send data: `fleetpulse run -c configs/scenarios/scenario_21.yaml -d 7200 -s firehose --stream-name fleetpulse-telemetry --region us-west-2 --profile fleetpulse` (repeat for seeds 21 to 25 and `configs/predictive.yaml`; use `--stream-name fleetpulse-telemetry-parquet` for the Parquet path).
6. Wait a few minutes; run `03_verify.sql`, `04` to `06`, `07_roles.sql`, then `08` to `10`; train with `python -m analytics.train_model`.
7. Alerting:
   - Generate a key pair (`openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -nocrypt -out alert_key.p8`, then `openssl rsa -in alert_key.p8 -pubout -out alert_key.pub`), paste the public key into `12_alerting.sql`, run it.
   - `./scripts/build_lambda.sh`, set `alert_email` in `terraform.tfvars`, `terraform apply`, confirm the SNS subscription email.
   - `python scripts/put_alert_secret.py --key-file alert_key.p8 --profile fleetpulse --region us-west-2`.
   - Test: refresh `STG_TELEMETRY` then `ML.FEATURES_1MIN` after a new run, then `aws lambda invoke --function-name fleetpulse-alerts out.json`. A second invoke should return 0.
   - Set `alert_schedule_state = "ENABLED"` only while demoing.
8. EC2 producer: push the repo, set `ec2_repo_url` (and optionally `ec2_run_args`) in `terraform.tfvars`, `terraform apply`. The instance stops itself when the run ends.
9. Compare formats: `snowflake/14_compare_formats.sql`.

## Capture evidence before teardown
Save into `docs/results/`:
- Snowflake: download CSVs from `snowflake/11_export_results.sql` and `14_compare_formats.sql`.
- Screenshots: S3 partitioned files (JSON and Parquet), Firehose streams, `SYSTEM$PIPE_STATUS`, silent robots, the warning-time result, the alert email, and the `lambda invoke` output showing 5 new alerts then 0.
- Terminal: training output to `docs/results/train_output.txt`.

## Teardown (in this order)
```sql
-- Snowflake, as ACCOUNTADMIN
DROP USER IF EXISTS FLEETPULSE_ALERT_SVC;
DROP ROLE IF EXISTS FLEETPULSE_ALERT_ROLE;
DROP DATABASE IF EXISTS FLEETPULSE;            -- pipes, stages, tables, dynamic tables, views, ML schema
DROP WAREHOUSE IF EXISTS FLEETPULSE_WH;
DROP RESOURCE MONITOR IF EXISTS FLEETPULSE_RM;
DROP STORAGE INTEGRATION IF EXISTS FLEETPULSE_S3_INT;
DROP ROLE IF EXISTS FLEETPULSE_DEV;
```
```bash
# AWS: empty the bucket first (covers raw/ and raw_parquet/), or destroy fails
aws s3 rm s3://fleetpulse-raw-<ACCOUNT_ID>-us-west-2 --recursive --profile fleetpulse
cd infra/aws && terraform destroy    # Firehose, Glue, Lambda, SNS, EventBridge, secret, EC2 and IAM
rm -f alert_key.p8 alert_key.pub     # local copies of the Snowflake service key
```
Verify nothing is left:
```bash
aws s3 ls --profile fleetpulse
aws firehose list-delivery-streams --region us-west-2 --profile fleetpulse
aws lambda list-functions --region us-west-2 --profile fleetpulse --query "Functions[?starts_with(FunctionName,'fleetpulse')]"
aws ec2 describe-instances --region us-west-2 --profile fleetpulse --filters Name=tag:Name,Values=fleetpulse-simulator --query "Reservations[].Instances[].State.Name"
aws secretsmanager list-secrets --region us-west-2 --profile fleetpulse --query "SecretList[?starts_with(Name,'fleetpulse')]"
aws iam list-roles --profile fleetpulse --query "Roles[?starts_with(RoleName,'fleetpulse')]"
```
Hygiene: deactivate or delete the access keys of the working IAM user, keep the budget alert, and check the billing page the next day.
