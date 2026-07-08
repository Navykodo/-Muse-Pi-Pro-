# Open Source Checklist

本清单用于发布前确认仓库没有暴露比赛设备、个人路径或凭证信息。

## GitHub Repository Settings

- License: `GNU General Public License v3.0`
- Do not initialize another README or LICENSE on GitHub if pushing this repository directly.
- Keep repository visibility private until the final scan passes.

## Required Files

- `README.md`
- `LICENSE`
- `THIRD_PARTY_LICENSES.md`
- `.gitignore`

## Sensitive Information Scan

发布前重新执行：

```bash
find APP g q r -type f -exec grep -nI -E '/(home|Users|root)/|/dev/serial/by-id/|MIIO_PLUG_TOKEN=[^<[:space:]][^[:space:]]*|MIIO_PLUG_IP=[^<[:space:]][^[:space:]]*|VISION_API_KEY=[^<[:space:]][^[:space:]]*|XFYUN_TTS_API_KEY=[^[:space:]]+|XFYUN_TTS_API_SECRET=[^[:space:]]+' {} +
```

```bash
find APP g q r -type f -exec grep -nI -E 'BEGIN (RSA|OPENSSH|DSA|EC|PRIVATE) KEY|AWS_SECRET_ACCESS_KEY|GITHUB_TOKEN|GH_TOKEN|OPENAI_API_KEY|ANTHROPIC_API_KEY|DASHSCOPE_API_KEY|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z_-]{20,}' {} +
```

正常情况下不应有真实值命中。允许出现 `<plug-token>`、`<plug-ip>`、`<不要写进仓库的 API Key>` 这类占位符。

## Files That Must Not Be Published

- `.env`
- `.venv/`
- `venv/`
- `__pycache__/`
- `*.pyc`
- `logs/`
- `output/`
- `maps/`
- `shots/`
- `*.o`
- `*.ko`
- `*.so`
- `*.cmd`
- `Module.symvers`
- `modules.order`
- 本地编译出的可执行文件

## Recommended Publish Flow

```bash
git status --short
git add -A
git commit -m "Prepare competition code for open source release"
git remote add origin <github-repo-url>
git push -u origin main
```

如果只是生成源码包，建议先 commit 后使用：

```bash
git archive --format=tar.gz -o ../competition_robot_source.tar.gz HEAD
```

不要直接压缩整个工作目录。
