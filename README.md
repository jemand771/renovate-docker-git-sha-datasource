# renovate-docker-git-sha-datasource

A [Renovate custom datasource](https://docs.renovatebot.com/modules/datasource/custom/) for
container images tagged with git commit SHAs.

Renovate cannot order SHA tags, and following a branch with the `git-refs` datasource proposes
commits whose image was never built or pushed to the repository in question. This service joins
both: it walks the branch history and returns the newest commit that exists as a tag in the given
image repository.

Example: the branch has commits `aaa`, `bbb`, `ccc`; `registry/build/app` has all three tags,
`registry/release/app` only `aaa` and `bbb`. Two references both pinned to `aaa` get separate
updates, `build/app` to `ccc` and `release/app` to `bbb`.

## API

`GET /releases?image=<registry>/<name>&repository=<git url>&branch=<branch>[&current=<sha>]`

```json
{"releases": [{"version": "<branch>", "digest": "<sha>"}]}
```

- Only the first-parent history of `branch` is considered, so commits of merged feature branches
  are never proposed on their own.
- If `current` is given and is not an ancestor of the newest tagged commit (e.g. a hotfix pin),
  `current` is returned unchanged, so updates never go sideways or backwards.
- `404` for an unknown image or branch, `422` for invalid parameters, `502` if git or the
  registry fail.

`GET /healthz` for readiness checks.

## Configuration

| Variable             | Default                      | Description                                |
|----------------------|------------------------------|--------------------------------------------|
| `CACHE_DIR`          | `$TMPDIR/git-sha-datasource` | Bare, commit-only clones of repositories   |
| `GIT_FETCH_INTERVAL` | `60`                         | Seconds before a cached clone is refetched |

Registry credentials are read from the first of `$REGISTRY_AUTH_FILE`,
`$XDG_RUNTIME_DIR/containers/auth.json`, `~/.config/containers/auth.json` and
`$DOCKER_CONFIG/config.json` that has an `auth` entry for the registry host, i.e. what
`podman login` or `docker login` write. Credential helpers are not supported.

Git runs as the `git` CLI, so SSH keys, `GIT_SSH_COMMAND` and `~/.ssh/config` apply as usual.
The service clones any URL it is asked for and is meant to run next to Renovate, not exposed;
restrict transports with `GIT_ALLOW_PROTOCOL` (e.g. `ssh`) if needed.

## Renovate configuration

The datasource only receives `packageName` and `currentValue`, so the image, repository and current
SHA travel in `packageName` and the branch in `currentValue`. The version is the branch name, which
needs a versioning scheme that accepts it; updates are then digest updates, one per image.

```json
{
  "customDatasources": {
    "git-sha": {
      "defaultRegistryUrlTemplate": "http://localhost:8080/releases?{{{packageName}}}&branch={{currentValue}}"
    }
  },
  "customManagers": [
    {
      "customType": "regex",
      "managerFilePatterns": ["/\\.ya?ml$/"],
      "matchStrings": [
        "(?<image>registry\\.example/[^:\\s\"']+):(?<currentDigest>[a-f0-9]{40})\\b[^\\n]*#\\s*renovate:\\s+repository=(?<repository>\\S+)\\s+branch=(?<currentValue>\\S+)"
      ],
      "datasourceTemplate": "custom.git-sha",
      "depNameTemplate": "{{{image}}}",
      "packageNameTemplate": "image={{{encodeURIComponent image}}}&repository={{{encodeURIComponent repository}}}&current={{{currentDigest}}}",
      "versioningTemplate": "regex:^(?<compatibility>.*?)(?<major>\\d+)?$"
    }
  ]
}
```

```yaml
app_image: registry.example/release/app:aaa...  # renovate: repository=git@git.example:org/app.git branch=main
```

Renovate refuses requests to internal hosts such as `localhost` by default. Allow it in the global
(self-hosted admin) configuration, it is rejected in repository configuration:

```sh
RENOVATE_HOST_RULES='[{"matchHost": "localhost", "allowInternal": true}]'
```

## Deployment

The image runs the service on port 8080 as UID 10001. Running it as a sidecar for the duration of
a Renovate run is enough; nothing needs to persist between runs:

```sh
podman pod create --name renovate
podman run -d --pod renovate -v ./auth.json:/auth.json:ro -e REGISTRY_AUTH_FILE=/auth.json \
  -v ~/.ssh:/home/app/.ssh:ro <this image>
# wait until http://localhost:8080/healthz answers, lookups fail otherwise
podman run --rm --pod renovate -e RENOVATE_HOST_RULES=... ghcr.io/renovatebot/renovate
podman pod rm -f renovate
```

## Development

```sh
nix-shell
pytest
ruff check && ruff format --check && ty check
```
