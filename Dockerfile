FROM public.ecr.aws/lambda/python:3.11

WORKDIR ${LAMBDA_TASK_ROOT}

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ app/
COPY web/ web/
COPY config.yaml .
COPY lambda_handler.py .

# Expose static web files path for FastAPI static mount
# (FastAPI will serve from web/ directory)

CMD ["lambda_handler.handler"]
