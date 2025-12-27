# SGP 协议 (ShadowGuard Protocol)

**作者:** JCHSH  
**架构:** DWT-DCT-QIM 混合水印系统

[English](README_EN.md) | 简体中文

---

## 📖 概述

SGP (ShadowGuard Protocol) 是基于 **DWT-DCT-QIM 混合架构**的高鲁棒性隐形水印协议，专为抵抗 JPEG 压缩和社交媒体传播（微信/Discord）而设计。具备母带/分发分离、96位 ID 支持和智能 DRM 鉴权功能。

### 核心特性

✅ **隐形注入**: 肉眼不可见的水印嵌入 (PSNR > 40dB)  
✅ **抗压缩**: 抵抗 JPEG Quality 60+ 和社交媒体压缩  
✅ **元数据搬运**: PNG 元数据无损保留 (兼容 SillyTavern)  
✅ **Discord Bot 支持**: 异步友好 API 与并发处理  
✅ **96位双重追踪**: 原作者 + 当前上传者追溯  
✅ **自适应 Top-N 策略**: 所有图像类型稳定 5.0x 冗余度

---

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

**依赖库:**
- `opencv-python>=4.8.0` - 图像处理
- `numpy>=1.24.0` - 矩阵运算
- `PyWavelets>=1.4.0` - 离散小波变换 (DWT)
- `scipy>=1.11.0` - 科学计算
- `Pillow>=10.0.0` - 元数据处理
- `discord.py>=2.4.0` - Discord Bot 框架（可选）
- `tqdm>=4.66.0` - 进度显示

### 2. 配置系统

⚠️ **首次部署必读**：请务必生成唯一的强密钥，不要使用默认值！

创建 `config.ini` 文件：

```ini
# ============================================================================
# ShadowGuard Protocol (SGP) 配置文件
# ============================================================================
# 使用场景说明：
#   - CLI 模式：通过 main.py 命令行工具使用（sign/verify/audit）
#   - Bot 模式：通过 bot.py Discord Bot 使用
# ============================================================================

[Discord]
# ┌─ 仅用于 Bot 模式 ─┐
# Discord Bot Token（从 Discord Developer Portal 获取）
bot_token = YOUR_DISCORD_BOT_TOKEN_HERE

[Security]
# ┌─ CLI 和 Bot 模式共用 ─┐
# ⚠️ 重要：请生成您自己的唯一密钥，不要使用默认值！
# 建议使用 32+ 字符的强随机密钥
watermark_key = CHANGE_THIS_TO_YOUR_OWN_SECRET_KEY_32CHARS_OR_MORE

[Identity]
# ┌─ 仅用于 CLI 模式 ─┐
# CLI 模式默认 UID（main.py sign 命令使用）
# ⚠️ Bot 模式不使用此配置，会自动使用 interaction.user.id
# 支持最大 25 位十进制数字
owner_uuid = 0

[Permissions]
# ┌─ 仅用于 CLI 模式默认值 ─┐
# CLI 模式：使用这些默认权限值
# ⚠️ Bot 模式不使用此配置，权限存储在数据库中（每个卡片独立配置）
allow_reprint = false       # 是否允许转载
allow_derivative = false    # 是否允许二次创作

[Algorithm]
# ┌─ CLI 和 Bot 模式共用 ─┐
# 算法参数（通常不需要修改）
qim_step = 40.0            # QIM 量化步长 (30-50 推荐)

[Paths]
# ┌─ CLI 和 Bot 模式共用 ─┐
# 目录配置
master_dir = storage/masters          # 母带存储目录
dist_dir = storage/distribution       # 分发临时目录（Bot 模式用后即删，CLI 模式持久化）
input_dir = input_images              # 输入图片目录（CLI 模式使用）

[System]
# ┌─ CLI 和 Bot 模式共用 ─┐
# 系统配置
workers = 4                # 并发进程数（建议设置为 CPU 核心数）
auto_cleanup = true        # 自动清理临时文件
```

**配置项使用说明：**

| 配置段 | CLI 模式 | Bot 模式 | 说明 |
|--------|---------|---------|------|
| `[Discord]` | ❌ 不使用 | ✅ 必需 | Bot Token 配置 |
| `[Security]` | ✅ 必需 | ✅ 必需 | 水印密钥（共享） |
| `[Identity]` | ✅ 使用 | ❌ 不使用 | CLI 默认 UID，Bot 自动使用 Discord User ID |
| `[Permissions]` | ✅ 使用 | ❌ 不使用 | CLI 默认权限，Bot 使用数据库存储 |
| `[Algorithm]` | ✅ 使用 | ✅ 使用 | 算法参数（共享） |
| `[Paths]` | ✅ 使用 | ✅ 使用 | 全局路径配置（共享） |
| `[System]` | ✅ 使用 | ✅ 使用 | 系统配置（共享） |

**密钥生成方法：**
```python
import secrets, string
key = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
print(f"Your watermark_key: {key}")
```

### 3. 运行系统

**CLI 模式:**

```bash
# 制作母带（将 input_images/ 中的图像加水印）
python main.py sign

# 生成用户分发版本
python main.py distribute -f filename_master.png -u 123456789012345678901234

# 检查水印（生成审计报告）
python main.py check -f image.png
```

**Discord Bot 模式:**

```bash
# 启动 Discord Bot
python bot.py
```

可用命令：
- `/上传角色卡` - 上传 PNG 并生成母带
- `/下载角色卡` - 获取带专属水印的副本
- `/管理角色卡` - 管理您上传的卡片
- `/审查角色卡` - 检查图片的追溯信息
- `/使用说明` - 查看详细帮助

### 使用方法

**创建母带:**
```bash
python main.py sign
```

**生成分发版本:**
```bash
python main.py distribute -f image_master.png -u 987654321098765432109876
```

**检查水印:**
```bash
python main.py check storage/distribution/987654321098765432109876_a1b2c3d4.png
```

---

## 🏗️ 架构设计

### 母带/分发分离

```
┌─────────────┐      ┌──────────────┐      ┌─────────────┐
│  原始图像    │ ──▶ │   母带版本    │ ──▶ │  分发版本    │
│             │      │ Current=0     │      │ Current=UID  │
└─────────────┘      └──────────────┘      └─────────────┘
                     存档母版              用户专属副本
```

**母带版本:**
- Current_UID = 0 (母带标识)
- 受保护的存档版本
- 四场景 DRM 鉴权

**分发版本:**
- Current_UID = 用户 ID
- 用户专属可追溯副本
- UUID4 防碰撞

### 96位双重追踪

**32 字节 Payload 结构:**
```
[字节 0-11]   原作者 UID          (96位, 最大 25 位十进制)
[字节 12-23]  当前上传者 UID      (96位, 0=母带)
[字节 24]     标志位              (Bit1=转载权, Bit0=二创权)
[字节 25-31]  填充                (保留)
```

---

## 🔬 算法原理

### DWT + DCT + QIM 混合

```
原始图像
   ↓
YCrCb 转换 → Y 通道
   ↓
DWT 分解 → HL 子带
   ↓
8x8 分块 → DCT 变换
   ↓
QIM 调制 → 水印嵌入
   ↓
IDCT → IDWT → 通道合并
   ↓
水印图像
```

### 自适应 Top-N 策略 (V5.0)

SGP 1.0 采用**自适应 Top-N 块选择**策略，替代固定方差阈值：

1. 收集所有 8x8 块的方差
2. 按方差降序排序
3. 选取 Top 2560 块（安全缓冲池）
4. 坐标锚定后使用前 1280 块嵌入
5. 确保所有图像类型稳定 5.0x 冗余度

**V5.0 双重修复:**
- **Safety Buffer (安全缓冲池)**: 2x 扩容防止排序抖动
- **Coordinate Anchoring (坐标锚定)**: 按 (row, col) 二次排序锁定顺序

**优势:**
- 有效处理平滑图像（动漫风格）
- 无论图像复杂度均保证最小冗余度
- 提取使用相同 Top-N 逻辑确保一致性

### 鲁棒性特性

✅ **JPEG 压缩**: Quality 60+ 提取成功率 > 95%  
✅ **社交媒体**: 抗微信/Discord/Twitter 压缩  
✅ **多尺度恢复**: 自动在 [512, 768, 1024, 1280, 2048] 分辨率重试  
✅ **冗余投票**: 列主序多数投票纠错  
✅ **密钥驱动随机**: SHA256 确定性块打乱

---

## 🤖 Bot 集成

### Discord Bot 示例

```python
import asyncio
from watermark_service import load_config, generate_distribution

async def bot_distribute_handler(user_id: int, master_filename: str):
    """Discord Bot 异步分发处理器"""
    config = load_config()
    
    # 线程池执行（非阻塞）
    loop = asyncio.get_event_loop()
    output_path = await loop.run_in_executor(
        None,
        generate_distribution,
        master_filename,
        user_id,
        config
    )
    
    return output_path

# Discord.py 用法
@bot.command()
async def get_image(ctx):
    user_id = ctx.author.id
    output_path = await bot_distribute_handler(user_id, "character_master.png")
    await ctx.send(file=discord.File(output_path))
```

### 纯函数 API

```python
from watermark_service import load_config, create_master_copy, generate_distribution

# 加载配置
config = load_config()

# 创建母带（并发安全）
master_path = create_master_copy("input.png", config)

# 生成分发版本（并发安全）
dist_path = generate_distribution("character_master.png", 123456789, config)
```

---

## 🔐 DRM 鉴权

### 四场景模型

**场景 A: 无水印 → 创建新母带**
```python
if payload is None:
    new_payload = WatermarkPayload(owner_uuid, NULL_UID, ...)
```

**场景 B: 原作者 → 更新母带**
```python
elif payload.original_uid == owner_uuid:
    new_payload = WatermarkPayload(owner_uuid, NULL_UID, ...)
```

**场景 C: 他人 + 允许二创 → Fork 分叉**
```python
elif payload.allow_derivative:
    new_payload = WatermarkPayload(owner_uuid, NULL_UID, ...)
```

**场景 D: 他人 + 禁止二创 → 拒绝**
```python
else:
    raise PermissionError("原作者禁止二次创作")
```

---

## 📊 性能指标

### 质量指标

| 指标 | 数值 | 状态 |
|------|------|------|
| **不可见性** | PSNR > 40dB | ✅ 优秀 |
| **鲁棒性** | JPEG Q60 > 95% | ✅ 强 |
| **容量** | 32 字节 (256 bits) | ✅ 双 UID + 标志位 |
| **置信度** | > 90% | ✅ 冗余投票 |
| **冗余度** | 5.0x (1280 块) | ✅ 稳定 |

### 处理速度

- **单图像**: ~2-3 秒 (512×512)
- **并发**: 4 核心 3.5× 加速
- **吞吐量**: ~20-30 图像/分钟 (4核 CPU)

---

## 📁 项目结构

```
cloud-backend/
├── config.ini              # 配置文件
├── main.py                 # CLI 入口
├── requirements.txt        # 依赖库
├── README.md               # 英文文档
├── README_ZH.md            # 中文文档
├── src/
│   ├── watermark_core.py   # DWT+DCT+QIM 算法
│   ├── watermark_service.py # 业务逻辑
│   └── audit_service.py    # 数字取证
├── storage/
│   ├── masters/            # 母带存档
│   └── distribution/       # 分发临时文件
├── input_images/           # 输入目录
└── output_reports/         # 审计报告
```

---

## 🎯 应用场景

### Discord 社区管理
- 作者创建母带存档
- 系统生成用户专属副本
- 追踪图像传播路径
- 识别泄露源头

### SillyTavern 角色卡保护
- 嵌入作者 ID 防盗窃
- 支持二创授权（Fork 模式）
- 保留角色卡元数据 (chara 字段)
- 追踪二创作品链

### 版权保护与取证
- 隐形 DRM 自动拦截
- 母带/分发分离精准追踪
- 法律证据支持（数字报告）
- 双重追踪（原作者 + 当前持有者）

---

## 🎓 技术细节

### 密钥驱动随机

```python
# 确定性种子生成（Anti-Scrubbing）
seed = SHA256(watermark_key + width + height)
rng = Random(seed)
rng.shuffle(block_coordinates)
```

- 相同密钥 + 图像尺寸 → 相同块打乱序列
- 不同密钥 → 完全不同的打乱
- 攻击者无法从嵌入位置推断水印内容

### 多尺度恢复

自动在多个分辨率重试提取：

```python
target_sizes = [512, 768, 1024, 1280, 2048]
for size in target_sizes:
    resized = cv2.resize(img, ..., interpolation=INTER_LANCZOS4)
    payload, confidence = extract_at_scale(resized, ...)
```

确保缩放/压缩后仍能成功提取。

---

## 📚 API 参考

### `load_config(config_path='config.ini') -> Dict[str, Any]`
加载配置文件（支持热重载）

### `create_master_copy(file_path: str, config: Dict) -> str`
创建母带版本（并发安全）

**参数:**
- `file_path`: 输入文件路径
- `config`: 配置字典

**返回:** 母带文件路径

**异常:**
- `PermissionError`: 禁止二次创作（场景 D）
- `ValueError`: 文件读取失败

### `generate_distribution(master_filename: str, user_uuid: int, config: Dict) -> str`
生成分发副本（并发安全 + 防碰撞）

**参数:**
- `master_filename`: 母带文件名（仅名称）
- `user_uuid`: 目标用户 UUID（支持 25 位）
- `config`: 配置字典

**返回:** 分发文件完整路径

**异常:**
- `ValueError`: 母带不存在或无水印

---

## 🔄 版本历史

### 1.0 (2025-12-27)
- ✅ **V5.0 协议升级**: Protocol Magic + CRC32 双重校验
- ✅ **安全缓冲池**: 2x 扩容 + 坐标锚定防排序抖动
- ✅ **自适应 Top-N 策略**: 稳定 5.0x 冗余度
- ✅ **项目清洗**: 专业开源项目发布准备

### V4.0
- 母带/分发分离架构
- 96位大整数支持
- 四场景 DRM 模型

### V3.0
- DWT+DCT+QIM 混合算法
- 元数据保护（SillyTavern）

---

## 📜 许可证

**Aladdin 免费公共许可证 (AFPL) 版本 9**

版权所有 (c) 2025 JCHSH。保留所有权利。

本项目采用 **Aladdin 免费公共许可证 (AFPL) 版本 9** 授权。

**要点说明:**
- ✅ **允许**: 非商业用途的使用、修改和分发
- ❌ **限制**: 未经许可的商业分发和使用
- ⚠️ **要求**: 保留版权声明并以相同许可证分发

**完整许可证文本:** 请查看 [LICENSE_ZH.md](LICENSE_ZH.md) 文件以了解完整条款和条件。

**重要提示:**  
这不是开源许可证。虽然它允许非商业用途的免费使用，但它限制商业分发。如需商业许可，请联系项目维护者。

---

## 🙏 致谢

感谢以下开源项目：
- **OpenCV** - 计算机视觉库
- **PyWavelets** - 小波变换库
- **NumPy** - 数值计算库
- **Pillow** - Python 图像库
- **SciPy** - 科学计算库

---

**Built with ❤️ by JCHSH**  
*SGP Protocol - 高鲁棒性隐形水印系统*
