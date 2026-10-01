FROM python:3.12-alpine AS build

COPY requirements.txt .
RUN pip install --no-cache-dir --no-compile --prefix=/install -r requirements.txt && \
    # botocore ships API models for every AWS service; only S3 is used
    find /install/lib/python3.12/site-packages/botocore/data -mindepth 1 -maxdepth 1 -type d ! -name s3 -exec rm -rf {} +

FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apk upgrade --no-cache

WORKDIR /app

COPY --from=build /install /usr/local
COPY app/ .

USER nobody
EXPOSE 8000

ENTRYPOINT ["python", "app.py"]
