#!/usr/bin/env bash
# Deploy AN3 Migration Dashboard to Lambda — no Docker, no CloudFormation.
# Each step is a direct AWS CLI call with clear errors.
#
# Usage:
#   export AWS_ACCESS_KEY_ID=...
#   export AWS_SECRET_ACCESS_KEY=...
#   export AWS_SESSION_TOKEN=...
#   export AWS_DEFAULT_REGION=us-east-1   # optional, defaults to us-east-1
#   bash deploy/deploy-lambda.sh

set -euo pipefail

FUNCTION_NAME="platform-master-lambda-dev-PlatformMasterLambda"
ROLE_NAME="an3-migration-dashboard-role"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$APP_DIR/build"

# ── Helpers ───────────────────────────────────────────────────────────────────
info() { echo ""; echo "▶  $*"; }
ok()   { echo "   ✓  $*"; }
warn() { echo "   ⚠  $*"; }
die()  { echo ""; echo "✗  $*" >&2; exit 1; }

# ── Pre-flight ────────────────────────────────────────────────────────────────
info "Checking prerequisites..."
command -v aws     &>/dev/null || die "aws cli not found.  brew install awscli"
command -v python3 &>/dev/null || die "python3 not found."
command -v zip     &>/dev/null || die "zip not found.  brew install zip"

AWS_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text) \
  || die "AWS credentials invalid. Set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN"
ok "account=$AWS_ACCOUNT  region=$AWS_REGION"

S3_BUCKET="an3-migration-data-${AWS_ACCOUNT}"
ZIP_KEY="code/function.zip"

# ── 1. S3 bucket ──────────────────────────────────────────────────────────────
info "1/6  S3 bucket..."
if aws s3api head-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION" 2>/dev/null; then
  ok "already exists: $S3_BUCKET"
else
  if [[ "$AWS_REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION" \
      --create-bucket-configuration LocationConstraint="$AWS_REGION" >/dev/null
  fi
  aws s3api put-bucket-versioning --bucket "$S3_BUCKET" \
    --versioning-configuration Status=Enabled
  ok "created: $S3_BUCKET"
fi

# ── 2. Build zip ──────────────────────────────────────────────────────────────
info "2/6  Building zip..."
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/pkg"

python3 -m pip install \
  --quiet \
  --target "$BUILD_DIR/pkg" \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.11 \
  --only-binary :all: \
  -r "$APP_DIR/requirements.txt"

cp -r "$APP_DIR/app"              "$BUILD_DIR/pkg/app"
cp -r "$APP_DIR/web"              "$BUILD_DIR/pkg/web"
cp    "$APP_DIR/lambda_handler.py" "$BUILD_DIR/pkg/lambda_handler.py"
cp    "$APP_DIR/config.yaml"      "$BUILD_DIR/pkg/config.yaml"

find "$BUILD_DIR/pkg" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR/pkg" -name "*.pyc" -delete 2>/dev/null || true

(cd "$BUILD_DIR/pkg" && zip -q -r "$BUILD_DIR/function.zip" .)
ok "built $(du -sh "$BUILD_DIR/function.zip" | cut -f1)"

# ── 3. Upload zip ─────────────────────────────────────────────────────────────
info "3/6  Uploading zip to S3..."
aws s3 cp "$BUILD_DIR/function.zip" "s3://${S3_BUCKET}/${ZIP_KEY}"
ok "s3://${S3_BUCKET}/${ZIP_KEY}"

# ── 4. IAM role — reuse existing function's role ─────────────────────────────
info "4/6  IAM role..."

# Get the role already attached to this Lambda — no need to create one
ROLE_ARN=$(aws lambda get-function-configuration \
  --function-name "$FUNCTION_NAME" --region "$AWS_REGION" \
  --query 'Role' --output text)
ROLE_NAME_CURRENT=$(echo "$ROLE_ARN" | sed 's|.*/||')
ok "reusing role: $ROLE_ARN"


# ── 5. Lambda function ────────────────────────────────────────────────────────
info "5/6  Lambda function..."
ENV_VARS="Variables={DB_PATH=/tmp/migration.db,OAUTH_TOKEN_JSON=/tmp/secrets/google-token.json,S3_DATA_BUCKET=${S3_BUCKET}}"

# Back up existing code before overwriting
BACKUP_KEY="code/backup-original-$(date +%Y%m%d%H%M%S).zip"
EXISTING_CODE_URL=$(aws lambda get-function \
  --function-name "$FUNCTION_NAME" --region "$AWS_REGION" \
  --query 'Code.Location' --output text 2>/dev/null || echo "")
if [[ -n "$EXISTING_CODE_URL" && "$EXISTING_CODE_URL" != "None" ]]; then
  curl -s -o "$BUILD_DIR/original-backup.zip" "$EXISTING_CODE_URL"
  aws s3 cp "$BUILD_DIR/original-backup.zip" "s3://${S3_BUCKET}/${BACKUP_KEY}" >/dev/null
  ok "backed up original code to s3://${S3_BUCKET}/${BACKUP_KEY}"
fi

# Update code (runtime + handler change included in config update)
aws lambda update-function-code \
  --function-name "$FUNCTION_NAME" \
  --s3-bucket "$S3_BUCKET" \
  --s3-key "$ZIP_KEY" \
  --region "$AWS_REGION" \
  --query 'FunctionArn' --output text
aws lambda wait function-updated \
  --function-name "$FUNCTION_NAME" --region "$AWS_REGION"

aws lambda update-function-configuration \
  --function-name "$FUNCTION_NAME" \
  --runtime python3.11 \
  --handler lambda_handler.handler \
  --timeout 30 \
  --memory-size 512 \
  --environment "$ENV_VARS" \
  --region "$AWS_REGION" \
  --query 'FunctionArn' --output text
aws lambda wait function-updated \
  --function-name "$FUNCTION_NAME" --region "$AWS_REGION"
ok "updated Lambda (runtime=python3.11, handler=lambda_handler.handler)"

# Wait until function is Active
aws lambda wait function-active \
  --function-name "$FUNCTION_NAME" --region "$AWS_REGION"
ok "Lambda is active"

# ── 5b. Function URL ──────────────────────────────────────────────────────────
# Allow public invocation
aws lambda add-permission \
  --function-name "$FUNCTION_NAME" \
  --statement-id FunctionURLPublicAccess \
  --action lambda:InvokeFunctionUrl \
  --principal "*" \
  --function-url-auth-type NONE \
  --region "$AWS_REGION" 2>/dev/null || true  # ignore if already exists

# Create or get Function URL
FUNCTION_URL=$(aws lambda get-function-url-config \
  --function-name "$FUNCTION_NAME" --region "$AWS_REGION" \
  --query 'FunctionUrl' --output text 2>/dev/null || echo "")

if [[ -z "$FUNCTION_URL" ]]; then
  FUNCTION_URL=$(aws lambda create-function-url-config \
    --function-name "$FUNCTION_NAME" \
    --auth-type NONE \
    --cors '{"AllowOrigins":["*"],"AllowMethods":["*"],"AllowHeaders":["*"]}' \
    --region "$AWS_REGION" \
    --query 'FunctionUrl' --output text)
  ok "created Function URL"
else
  ok "Function URL exists"
fi

# ── 6. Encode Google credentials into Lambda env vars ────────────────────────
info "6/6  Setting Google credentials as Lambda env vars..."

EXTRA_ENV=""

# Prefer service account (no browser login needed) — simpler for Lambda
if [[ -f "$APP_DIR/secrets/google-service-account.json" ]]; then
  SA_B64=$(base64 -b 0 -i "$APP_DIR/secrets/google-service-account.json")
  EXTRA_ENV+="GOOGLE_SA_BASE64=${SA_B64},"
  EXTRA_ENV+="SERVICE_ACCOUNT_JSON=/tmp/secrets/google-service-account.json,"
  ok "encoded google-service-account.json"
elif [[ -f "$APP_DIR/secrets/google-token.json" ]]; then
  TOKEN_B64=$(base64 -b 0 -i "$APP_DIR/secrets/google-token.json")
  EXTRA_ENV+="GOOGLE_TOKEN_BASE64=${TOKEN_B64},"
  ok "encoded google-token.json (OAuth)"
else
  warn "No Google credentials found."
  warn "Create secrets/google-service-account.json (see README) and re-run."
fi

if [[ -n "$EXTRA_ENV" ]]; then
  FULL_ENV="Variables={DB_PATH=/tmp/migration.db,OAUTH_TOKEN_JSON=/tmp/secrets/google-token.json,${EXTRA_ENV%,}}"
  aws lambda update-function-configuration \
    --function-name "$FUNCTION_NAME" \
    --environment "$FULL_ENV" \
    --region "$AWS_REGION" \
    --query 'FunctionArn' --output text >/dev/null
  aws lambda wait function-updated \
    --function-name "$FUNCTION_NAME" --region "$AWS_REGION"
  ok "credentials baked into Lambda env vars"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅  DEPLOY COMPLETE"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Dashboard is live at:"
echo "    $FUNCTION_URL"
echo ""
LAMBDA_DOMAIN=$(echo "$FUNCTION_URL" | sed 's|https://||' | sed 's|/$||')
echo "  ─── Update CloudFront distribution EMG6IRP2QJ467 ─────────"
echo "  In the AWS CloudFront console, open distribution EMG6IRP2QJ467:"
echo ""
echo "  Origins tab → Edit origin (or Add origin):"
echo "    Origin domain : $LAMBDA_DOMAIN"
echo "    Protocol      : HTTPS only"
echo ""
echo "  Behaviors tab → Default (*) → Edit:"
echo "    Origin              : the Lambda origin above"
echo "    Allowed methods     : GET HEAD OPTIONS PUT POST PATCH DELETE"
echo "    Cache policy        : CachingDisabled"
echo "    Origin request      : AllViewerExceptHostHeader"
echo "    Viewer protocol     : Redirect HTTP to HTTPS"
echo ""
echo "  Save → wait ~2 min → https://delivery-migration.now3.amagi.tv is live"
echo ""
echo "  ─── Re-deploy after code changes ──────────────────────────"
echo "  bash deploy/deploy-lambda.sh"
echo ""
echo "  ─── View logs ──────────────────────────────────────────────"
echo "  aws logs tail /aws/lambda/$FUNCTION_NAME --follow --region $AWS_REGION"
echo ""
echo "═══════════════════════════════════════════════════════════════"
