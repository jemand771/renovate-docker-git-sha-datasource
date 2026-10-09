FROM python:3.14-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
COPY src src
RUN pip install --no-cache-dir . && rm -rf /app

RUN useradd --uid 10001 --create-home app \
 && install -d -o app /var/cache/git-sha-datasource
USER app
ENV CACHE_DIR=/var/cache/git-sha-datasource \
    PYTHONUNBUFFERED=1
WORKDIR /home/app

EXPOSE 8080
CMD ["uvicorn", "--factory", "renovate_docker_git_sha_datasource.app:create_app", "--host", "0.0.0.0", "--port", "8080"]
