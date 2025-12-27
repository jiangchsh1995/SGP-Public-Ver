# 🔐 SGP 水印系统 - 审计文档

## 致审计管理员

感谢您抽空审核本项目代码。本文档将帮助您快速理解项目结构并完成部署验证。

---

## ⚠️ 重要声明

**本项目包含专有算法实现，请务必保密：**

- **核心算法位于 `src/watermark_core.py`**，包含 DWT+DCT+QIM 混合水印技术
- **请勿泄露源代码**给非授权人员
- 仅供服务器管理员进行**安全审计和部署验证**
- 禁止复制、修改、分发或逆向工程

---

## 📁 项目结构

```
cloud-backend/
├── src/                        # 核心源代码（专有）
│   ├── watermark_core.py       # ⚠️ 核心算法实现（请勿泄露）
│   ├── watermark_service.py    # 水印服务接口
│   └── audit_service.py        # 审计报告生成
├── bot.py                      # Discord Bot 主程序
├── main.py                     # CLI 工具（可选）
├── config.ini                  # 配置文件（需自定义）
├── requirements.txt            # Python 依赖
└── storage/                    # 数据存储目录
    ├── masters/                # 母带存储
    └── distribution/           # 分发版本存储
```

---

## 🚀 快速部署指南

### 1. 环境准备

**系统要求：**
- Python 3.8+
- 推荐操作系统：Ubuntu 20.04+ / Windows 10+

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

**核心依赖：**
- `discord.py >= 2.4.0` - Discord Bot 框架
- `opencv-python` - 图像处理
- `PyWavelets` - 小波变换
- `Pillow` - PNG 元数据保留

### 3. 配置文件

**⚠️ 安全警告：首次运行请务必修改配置文件！**

编辑 `config.ini`：

```ini
[Discord]
bot_token = YOUR_DISCORD_BOT_TOKEN_HERE  # 替换为你的 Discord Bot Token

[Security]
# ⚠️ 关键：请生成新的水印密钥，不要使用默认值！
# 推荐使用随机生成的 32 字符字符串
watermark_key = CHANGE_ME_TO_RANDOM_STRING_32_CHARS
```

**密钥生成建议：**
```python
import secrets
print(secrets.token_urlsafe(32))  # 生成安全随机密钥
```

### 4. 运行 Bot

```bash
python bot.py
```

**首次启动检查：**
- ✅ Bot 成功连接到 Discord
- ✅ 自动创建 `storage/masters/` 和 `storage/distribution/` 目录
- ✅ 控制台输出 "Bot 已登录: YourBotName#1234"

---

## 🔍 功能验证

### Discord Slash Commands

Bot 启动后，在 Discord 服务器中使用以下命令：

1. **`/upload`** - 上传原始卡面制作母带
2. **`/download`** - 下载分发版本（自动嵌入用户水印）
3. **`/audit`** - 审计图像水印信息
4. **`/list`** - 查看当前服务器的所有卡面

### CLI 工具（可选）

如果需要本地测试水印功能：

```bash
# 制作母带
python main.py create input_images/test.png

# 生成分发版本
python main.py distribute test_master.png 123456789

# 审计检查
python main.py audit suspicious_image.png
```

---

## 🛡️ 安全注意事项

### 关于密钥管理

1. **永远不要提交真实密钥到 Git**
   - `.gitignore` 已配置排除 `config.ini`
   - 每个部署环境应使用独立的 `watermark_key`

2. **密钥轮换策略**
   - 如果怀疑密钥泄露，立即更换并重新制作所有母带
   - 旧密钥加密的水印无法用新密钥提取

3. **数据库备份**
   - 定期备份 `data.db`（记录卡面元数据）
   - 定期备份 `storage/masters/`（母带文件）

### 关于权限控制

- Bot 需要以下 Discord 权限：
  - `Send Messages` - 发送消息
  - `Attach Files` - 上传/下载文件
  - `Use Slash Commands` - 使用斜杠命令

---

## 📊 性能参数

**水印嵌入性能：**
- 1024x1024 图像：约 2-3 秒
- 2048x2048 图像：约 8-12 秒
- 4096x4096 图像：约 30-45 秒

**抗攻击能力：**
- ✅ 抗 JPEG 压缩（质量 > 85）
- ✅ 抗轻微缩放（±20%）
- ✅ 抗高斯噪声（σ < 5）
- ⚠️ 强裁剪/强滤镜可能破坏水印

---

## 🐛 故障排查

### Bot 无法启动

**问题：**`discord.errors.LoginFailure: Improper token has been passed.`

**解决：**检查 `config.ini` 中的 `bot_token` 是否正确。

---

### 水印提取失败

**问题：**下载的图像无法检测到水印。

**可能原因：**
1. 图像被第三方软件二次压缩
2. `watermark_key` 不匹配
3. 图像经过强滤镜处理

**解决：**
- 确保分发通道禁用自动压缩（Discord Nitro 用户可上传原图）
- 检查配置文件的密钥是否一致

---

### 数据库锁定

**问题：**`database is locked` 错误。

**解决：**
```bash
# 检查是否有多个 bot.py 实例运行
pkill -f bot.py

# 删除锁文件（如果存在）
rm data.db-journal
```

---

## 📞 技术支持

**审计期间如有疑问，请联系：**

- 开发者：JCHSH
- 项目仓库：GitHub Private Repository（仅限授权访问）

**请勿在公开渠道讨论核心算法细节。**

---

## ✅ 审计检查清单

完成以下检查后，即可批准部署：

- [ ] 所有依赖成功安装
- [ ] `config.ini` 已配置正确的 Bot Token 和密钥
- [ ] Bot 成功连接到 Discord
- [ ] `/upload` 命令可以制作母带
- [ ] `/download` 命令可以生成分发版本
- [ ] `/audit` 命令可以检测水印
- [ ] 水印提取置信度 > 80%
- [ ] `storage/` 目录权限正确（Bot 可读写）
- [ ] `.gitignore` 已排除敏感文件（`config.ini`, `*.key`, `storage/`）

---

**祝审计顺利！🎉**
