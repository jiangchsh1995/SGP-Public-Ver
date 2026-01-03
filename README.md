# SGP Protocol (ShadowGuard Protocol)

**Author:** JCHSH  
**Architecture:** DWT-DCT-QIM Hybrid Watermarking System

English | [简体中文](README_ZH.md)

---

## 📖 Overview

SGP (ShadowGuard Protocol) is a high-robustness invisible watermarking protocol based on **DWT-DCT-QIM hybrid architecture**, specifically designed to resist JPEG compression and social media propagation (WeChat/Discord). It features Master/Distribution separation, 96-bit ID support, and intelligent DRM authentication.

### Key Features

✅ **Invisible Injection**: Imperceptible watermark embedding (PSNR > 40dB)  
✅ **Compression Resistant**: Survives JPEG Quality 60+ and social media compression  
✅ **Metadata Transport**: Lossless PNG metadata preservation (SillyTavern compatible)  
✅ **Discord Bot Support**: Async-friendly API with concurrent processing  
✅ **96-bit Dual Tracking**: Original author + Current uploader tracing  
✅ **Adaptive Top-N Strategy**: Stable 5.0x redundancy for all image types

---

## 🚀 Quick Start

### Installation

```bash
pip install -r requirements.txt
```

**Dependencies:**
- `opencv-python` - Image processing
- `numpy` - Matrix operations
- `PyWavelets` - DWT (Discrete Wavelet Transform)
- `scipy` - Scientific computing
- `Pillow` - Metadata handling

### Configuration

Edit `config.ini`:

```ini
[Identity]
owner_uuid = 123456789012345678901234567  # Your 25-digit UUID

[Permissions]
allow_reprint = 1        # Allow redistribution
allow_derivative = 1     # Allow derivative works

[Algorithm]
qim_step = 40.0         # QIM quantization step (30-50)
```

### Usage

**Create Master Copy:**
```bash
python main.py sign
```

**Generate Distribution:**
```bash
python main.py distribute -f image_master.png -u 987654321098765432109876
```

**Check Watermark:**
```bash
python main.py check storage/distribution/987654321098765432109876_a1b2c3d4.png
```

---

## 🏗️ Architecture

### Master/Distribution Separation

```
┌─────────────┐      ┌──────────────┐      ┌─────────────┐
│  Raw Image  │ ──▶ │ Master Copy   │ ──▶ │ Distribution │
│             │      │ Current=0     │      │ Current=UID  │
└─────────────┘      └──────────────┘      └─────────────┘
                     Archive Version       User-Specific
```

**Master Copy:**
- Current_UID = 0 (Master identifier)
- Protected archive version
- 4-case DRM authentication

**Distribution:**
- Current_UID = User ID
- User-specific traceable copy
- UUID4 collision prevention

### 96-bit Dual Tracking

**32-Byte Payload Structure:**
```
[Byte 0-11]   Original Author UID    (96-bit, max 25 decimal digits)
[Byte 12-23]  Current Uploader UID   (96-bit, 0=Master)
[Byte 24]     Flags                  (Bit1=Reprint, Bit0=Derivative)
[Byte 25-31]  Padding                (Reserved)
```

---

## 🔬 Algorithm

### DWT + DCT + QIM Hybrid

```
Raw Image
   ↓
YCrCb Transform → Y Channel
   ↓
DWT Decomposition → HL Subband
   ↓
8x8 Blocks → DCT Transform
   ↓
QIM Modulation → Watermark Embedding
   ↓
IDCT → IDWT → Merge Channels
   ↓
Watermarked Image
```

### Adaptive Top-N Strategy (V4.2)

Instead of using fixed variance threshold, SGP V4.2 employs **adaptive Top-N block selection**:

1. Collect variance for all 8x8 blocks
2. Sort blocks by variance (descending)
3. Select top 1280 blocks (TARGET_REDUNDANCY × 256 bits)
4. Ensure stable 5.0x redundancy for all image types

**Benefits:**
- Handles smooth images (Anime style) effectively
- Guarantees minimum redundancy regardless of complexity
- Extraction uses identical Top-N logic for consistency

### Robustness Features

✅ **JPEG Compression**: Quality 60+ extraction success rate > 95%  
✅ **Social Media**: WeChat/Discord/Twitter resistant  
✅ **Multi-Scale Recovery**: Auto-retry at [512, 768, 1024, 1280, 2048] resolutions  
✅ **Redundant Voting**: Column-wise majority voting for error correction  
✅ **Key-Driven Randomness**: SHA256-based deterministic block shuffling

---

## 🤖 Bot Integration

### Discord Bot Example

```python
import asyncio
from watermark_service import load_config, generate_distribution

async def bot_distribute_handler(user_id: int, master_filename: str):
    """Async distribution handler for Discord Bot"""
    config = load_config()
    
    # Execute in thread pool (non-blocking)
    loop = asyncio.get_event_loop()
    output_path = await loop.run_in_executor(
        None,
        generate_distribution,
        master_filename,
        user_id,
        config
    )
    
    return output_path

# Discord.py usage
@bot.command()
async def get_image(ctx):
    user_id = ctx.author.id
    output_path = await bot_distribute_handler(user_id, "character_master.png")
    await ctx.send(file=discord.File(output_path))
```

### Pure Function API

```python
from watermark_service import load_config, create_master_copy, generate_distribution

# Load configuration
config = load_config()

# Create master copy (concurrent-safe)
master_path = create_master_copy("input.png", config)

# Generate distribution (concurrent-safe)
dist_path = generate_distribution("character_master.png", 123456789, config)
```

---

## 🔐 DRM Authentication

### 4-Case Model

**Case A: No Watermark → Create New Master**
```python
if payload is None:
    new_payload = WatermarkPayload(owner_uuid, NULL_UID, ...)
```

**Case B: Original Author → Update Master**
```python
elif payload.original_uid == owner_uuid:
    new_payload = WatermarkPayload(owner_uuid, NULL_UID, ...)
```

**Case C: Others + Allow Derivative → Fork**
```python
elif payload.allow_derivative:
    new_payload = WatermarkPayload(owner_uuid, NULL_UID, ...)
```

**Case D: Others + Forbid Derivative → Reject**
```python
else:
    raise PermissionError("Original author forbids derivative works")
```

---

## 📊 Performance

### Quality Metrics

| Metric | Value | Status |
|--------|-------|--------|
| **Invisibility** | PSNR > 40dB | ✅ Excellent |
| **Robustness** | JPEG Q60 > 95% | ✅ Strong |
| **Capacity** | 32 bytes (256 bits) | ✅ Dual UID + Flags |
| **Confidence** | > 90% | ✅ Redundant Voting |
| **Redundancy** | 5.0x (1280 blocks) | ✅ Stable |

### Processing Speed

- **Single Image**: ~2-3 seconds (512×512)
- **Concurrent**: 3.5× speedup with 4 cores
- **Throughput**: ~20-30 images/minute (4-core CPU)

---

## 📁 Project Structure

```
cloud-backend/
├── config.ini              # Configuration
├── main.py                 # CLI entry point
├── requirements.txt        # Dependencies
├── README.md               # Documentation
├── src/
│   ├── watermark_core.py   # DWT+DCT+QIM algorithm
│   ├── watermark_service.py # Business logic
│   └── audit_service.py    # Digital forensics
├── storage/
│   ├── masters/            # Master copies
│   └── distribution/       # Distribution temp files
├── input_images/           # Input directory
└── output_reports/         # Audit reports
```

---

## 🎯 Use Cases

### Discord Community Management
- Authors create master archives
- System generates user-specific copies
- Track image propagation paths
- Identify leak sources

### SillyTavern Character Card Protection
- Embed author ID to prevent theft
- Support derivative authorization (Fork mode)
- Preserve character card metadata (chara field)
- Track derivative work chains

### Copyright Protection & Forensics
- Invisible DRM with auto-rejection
- Master/Distribution separation for precise tracking
- Legal evidence support (digital reports)
- Dual tracking (Original + Current holder)

---

## 🎓 Technical Details

### Key-Driven Randomness

```python
# Deterministic seed generation (Anti-Scrubbing)
seed = SHA256(watermark_key + width + height)
rng = Random(seed)
rng.shuffle(block_coordinates)
```

- Same key + image size → Same block shuffle sequence
- Different key → Completely different shuffle
- Attackers cannot deduce watermark content from embedding positions

### Multi-Scale Recovery

Auto-retry extraction at multiple resolutions:

```python
target_sizes = [512, 768, 1024, 1280, 2048]
for size in target_sizes:
    resized = cv2.resize(img, ..., interpolation=INTER_LANCZOS4)
    payload, confidence = extract_at_scale(resized, ...)
```

Ensures extraction success even after resizing/compression.

---

## 📚 API Reference

### `load_config(config_path='config.ini') -> Dict[str, Any]`
Load configuration file (supports hot-reload)

### `create_master_copy(file_path: str, config: Dict) -> str`
Create master copy (concurrent-safe)

**Parameters:**
- `file_path`: Input file path
- `config`: Configuration dictionary

**Returns:** Master file path

**Exceptions:**
- `PermissionError`: Derivative work forbidden (Case D)
- `ValueError`: File read failure

### `generate_distribution(master_filename: str, user_uuid: int, config: Dict) -> str`
Generate distribution copy (concurrent-safe + collision-free)

**Parameters:**
- `master_filename`: Master filename (name only)
- `user_uuid`: Target user UUID (supports 25-digit)
- `config`: Configuration dictionary

**Returns:** Distribution file full path

**Exceptions:**
- `ValueError`: Master not found or no watermark

---

## 🔄 Version History

### 1.0 (2025-12-27)
- ✅ **V5.0 Protocol Upgrade**: Protocol Magic + CRC32 Checksum dual validation
- ✅ **Safety Buffer**: 2x expansion + Coordinate Anchoring to prevent sort jitter
- ✅ **Adaptive Top-N Strategy**: Stable 5.0x redundancy for all image types
- ✅ **Project Cleanup**: Professional open-source project release preparation

### V4.0
- Master/Distribution separation architecture
- 96-bit large integer support
- 4-case DRM model
- Bug fixes: tuple unpacking error

### V3.0
- DWT+DCT+QIM hybrid algorithm
- Metadata protection (SillyTavern)

---

## 📜 License

**Aladdin Free Public License (AFPL) Version 9**

Copyright (c) 2025 JCHSH. All Rights Reserved.

This project is licensed under the **Aladdin Free Public License (AFPL) Version 9**.

**Key Points:**
- ✅ **Allowed**: Non-commercial use, modification, and distribution
- ❌ **Restricted**: Commercial distribution and use without permission
- ⚠️ **Required**: Preserve copyright notices and distribute under same license

**Full License Text:** See [LICENSE](LICENSE) file for complete terms and conditions.

**Important Notice:**  
This is NOT an Open Source license. While it allows free use for non-commercial purposes, it restricts commercial distribution. If you need commercial licensing, please contact the project maintainer.

---

## 🙏 Acknowledgments

Thanks to the following open-source projects:
- **OpenCV** - Computer vision library
- **PyWavelets** - Wavelet transform library
- **NumPy** - Numerical computing library
- **Pillow** - Python imaging library
- **SciPy** - Scientific computing library

---

**Built with ❤️ by JCHSH**  
*SGP Protocol - High-Robustness Invisible Watermarking System*
