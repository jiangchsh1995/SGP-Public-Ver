# -------------------------------------------------------------------------
# PROPRIETARY SOURCE CODE / 专有源代码
# Copyright (c) 2025 JCHSH. All Rights Reserved.
#
# This code is provided for SECURITY AUDIT PURPOSES ONLY to authorized
# server administrators.
# Copying, modifying, distributing, or reverse engineering this code
# without written permission is strictly prohibited.
# -------------------------------------------------------------------------

import cv2
import numpy as np
import pywt
from scipy.fftpack import dct, idct
from dataclasses import dataclass
from typing import Optional, Tuple, List
import hashlib
import zlib

# 常量定义
NULL_UID = 0  # 母带标识

# SGP V5.0 协议配置
PROTOCOL_MAGIC = b'\x53\x47'  # 'SG' 协议签名
TARGET_REDUNDANCY = 5  # 目标冗余度：至少重复存 5 份
PAYLOAD_BITS = 256     # 32 字节 = 256 比特
MIN_BLOCKS = PAYLOAD_BITS * TARGET_REDUNDANCY  # 1280 个块
BUFFER_RATIO = 2.0     # V5.0 安全缓冲池比例（防止排序抖动）


@dataclass
class WatermarkPayload:
    """水印负载数据结构"""
    original_uid: int = 0      # 原始作者 UID (12 bytes, 支持 25 位十进制)
    current_uid: int = 0       # 当前持有者 UID (12 bytes)
    allow_reprint: bool = False
    allow_derivative: bool = False
    
    def is_master(self) -> bool:
        """判断是否为母带版本"""
        return self.current_uid == NULL_UID
    
    def to_bytes(self) -> bytes:
        """
        序列化为 32 字节 Payload (V5.0 协议头 + CRC32 校验)
        
        布局:
        [0-1]   Magic: b'\x53\x47' ('SG' 协议签名)
        [2-13]  Original UID (12 bytes)
        [14-25] Current UID (12 bytes)
        [26]    Flags (1 byte)
        [27-30] CRC32 Checksum (4 bytes)
        [31]    Padding (1 byte)
        """
        # 构建数据体（用于 CRC 计算）
        original_bytes = self.original_uid.to_bytes(12, byteorder='big')
        current_bytes = self.current_uid.to_bytes(12, byteorder='big')
        flags = (int(self.allow_reprint) << 0) | (int(self.allow_derivative) << 1)
        flags_byte = flags.to_bytes(1, byteorder='big')
        
        body = original_bytes + current_bytes + flags_byte  # 25 bytes
        
        # 计算 CRC32 校验值
        crc = zlib.crc32(body) & 0xFFFFFFFF
        crc_bytes = crc.to_bytes(4, byteorder='big')
        
        # 组装完整 Payload: Magic + Body + CRC + Padding
        payload = PROTOCOL_MAGIC + body + crc_bytes + b'\x00'
        
        return payload
    
    @staticmethod
    def from_bytes(data: bytes) -> Optional['WatermarkPayload']:
        """
        从 32 字节反序列化 (V5.0 双重校验防御)
        
        Returns:
            WatermarkPayload 对象，如果校验失败返回 None
        """
        if len(data) != 32:
            return None
        
        # 第一道防线：Magic 校验
        if data[0:2] != PROTOCOL_MAGIC:
            print(f"[校验] Magic 校验失败: 期望 {PROTOCOL_MAGIC.hex()}, 实际 {data[0:2].hex()}")
            return None
        
        # 第二道防线：CRC32 完整性校验
        body = data[2:27]  # 提取数据体 (25 bytes)
        stored_crc = int.from_bytes(data[27:31], byteorder='big')
        calculated_crc = zlib.crc32(body) & 0xFFFFFFFF
        
        if calculated_crc != stored_crc:
            print(f"[校验] CRC32 校验失败: 期望 {stored_crc:08X}, 实际 {calculated_crc:08X}")
            return None
        
        # 通过双重校验，解析数据
        original_uid = int.from_bytes(body[0:12], byteorder='big')
        current_uid = int.from_bytes(body[12:24], byteorder='big')
        flags = body[24]
        allow_reprint = bool(flags & 0x01)
        allow_derivative = bool(flags & 0x02)
        
        print(f"[校验] ✅ 协议校验通过 (Magic + CRC32)")
        
        return WatermarkPayload(
            original_uid=original_uid,
            current_uid=current_uid,
            allow_reprint=allow_reprint,
            allow_derivative=allow_derivative
        )


def _generate_seed(watermark_key: str, width: int, height: int) -> int:
    """
    密钥驱动的种子生成（Anti-Scrubbing 核心）
    
    Args:
        watermark_key: 系统私钥（从 config.ini 读取）
        width: 图像宽度
        height: 图像高度
    
    Returns:
        确定性随机种子
    """
    seed_str = f"{watermark_key}_{width}_{height}"
    hash_obj = hashlib.sha256(seed_str.encode('utf-8'))
    seed = int.from_bytes(hash_obj.digest()[:4], byteorder='big')
    return seed


def _get_valid_blocks(subband: np.ndarray, block_size: int = 8) -> List[Tuple[int, int]]:
    """
    自适应 Top-N 块选择策略（V5.0 安全缓冲池 + 坐标锚定）
    
    V5.0 双重修复策略：
    1. Safety Buffer（安全缓冲池）：扩大候选池到 2x，防止排序抖动
       - 嵌入水印会改变方差，可能导致块排名变化
       - 通过选取 Top-2N 块，确保即使方差微调，嵌入的块仍在池中
    
    2. Coordinate Anchoring（坐标锚定）：对池内块按坐标排序
       - 丢弃方差信息，按 (row, col) 二次排序
       - 确保 shuffle 输入列表在嵌入前后完全一致
    
    Args:
        subband: DWT 子带（HL/LH/HH）
        block_size: 块大小（默认 8x8）
    
    Returns:
        安全缓冲池的坐标列表（按坐标升序排列，大小为 MIN_BLOCKS * BUFFER_RATIO）
    """
    rows, cols = subband.shape
    num_rows = rows // block_size
    num_cols = cols // block_size
    
    # 收集所有块的方差和坐标
    all_blocks = []
    for row in range(num_rows):
        for col in range(num_cols):
            block = subband[row*block_size:(row+1)*block_size,
                           col*block_size:(col+1)*block_size]
            var = np.var(block)
            all_blocks.append((var, row, col))
    
    # 第一步：Safety Buffer（扩大候选池）
    # 选取 MIN_BLOCKS * BUFFER_RATIO 个块，而不是仅 MIN_BLOCKS 个
    buffer_size = int(MIN_BLOCKS * BUFFER_RATIO)
    buffer_size = min(buffer_size, len(all_blocks))
    
    sorted_blocks = sorted(all_blocks, key=lambda x: x[0], reverse=True)
    buffer_pool = sorted_blocks[:buffer_size]
    
    # 第二步：Coordinate Anchoring（坐标锚定）
    # 丢弃方差信息，按 (row, col) 升序排序
    # 这确保无论方差如何变化，列表顺序固定
    valid_coords = [(row, col) for var, row, col in buffer_pool]
    valid_coords.sort()  # 按 (row, col) 升序，锁定顺序
    
    return valid_coords


def embed_watermark(
    img: np.ndarray,
    payload: WatermarkPayload,
    watermark_key: str,
    qim_step: float = 40.0,
    var_threshold: float = None  # V4.2: 已废弃，保留参数兼容性
) -> np.ndarray:
    """
    嵌入水印（DWT + DCT + QIM + Key-Driven Randomness + Adaptive Top-N）
    
    Args:
        img: BGR 图像 (H, W, 3)
        payload: 水印负载
        watermark_key: 系统私钥（用于生成随机种子）
        qim_step: QIM 量化步长
        var_threshold: 已废弃（V4.2 使用 Top-N 策略）
    
    Returns:
        嵌入水印后的图像
    """
    h, w = img.shape[:2]
    
    # 转换为 YCrCb 色彩空间
    ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    Y = ycrcb[:, :, 0].astype(np.float32)
    
    # DWT 一级分解（Haar 小波）
    coeffs = pywt.dwt2(Y, 'haar')
    LL, (LH, HL, HH) = coeffs
    
    # 选择 HL 子带（水平细节）作为嵌入目标
    subband = HL.copy()
    
    # V5.0 安全缓冲池 + Top-N 块选择
    block_size = 8
    buffer_coords = _get_valid_blocks(subband, block_size)  # 返回 2x 缓冲池
    
    print(f"[嵌入] 图像尺寸: {w}x{h}, 缓冲池大小: {len(buffer_coords)}")
    
    # 序列化 Payload 为比特流
    payload_bytes = payload.to_bytes()
    bits = np.unpackbits(np.frombuffer(payload_bytes, dtype=np.uint8))
    total_bits_len = len(bits)
    
    # 生成密钥驱动的随机种子
    seed = _generate_seed(watermark_key, w, h)
    rng = np.random.RandomState(seed)
    
    # 乱序缓冲池（Key-Driven Randomness）
    shuffled_coords = buffer_coords.copy()
    rng.shuffle(shuffled_coords)
    
    # V5.0 关键：只使用前 MIN_BLOCKS 个块进行实际嵌入
    # 缓冲池确保这 MIN_BLOCKS 个块在嵌入前后保持一致
    target_coords = shuffled_coords[:MIN_BLOCKS]
    
    # Padding subband 到 8 的倍数
    rows, cols = subband.shape
    rows_pad = (block_size - rows % block_size) % block_size
    cols_pad = (block_size - cols % block_size) % block_size
    subband_pad = np.pad(subband, ((0, rows_pad), (0, cols_pad)), mode='edge')
    
    # 嵌入计数
    embedded_blocks = 0
    bit_idx = 0
    
    for row, col in target_coords:
        if bit_idx >= total_bits_len:
            bit_idx = 0  # 循环重复嵌入（冗余）
        
        # 提取 8x8 块
        block = subband_pad[row*block_size:(row+1)*block_size,
                           col*block_size:(col+1)*block_size].copy()
        
        # DCT 变换
        dct_block = dct(dct(block.T, norm='ortho').T, norm='ortho')
        
        # 随机选择中频系数位置（Key-Driven）
        mid_freq_positions = [(2, 1), (1, 2), (2, 2), (3, 1), (1, 3), (3, 2), (2, 3)]
        chosen_pos = mid_freq_positions[rng.randint(0, len(mid_freq_positions))]
        i, j = chosen_pos
        
        # QIM 量化索引调制
        coeff = dct_block[i, j]
        bit_val = bits[bit_idx]
        
        # 量化到最近的步长倍数
        quantized = np.round(coeff / qim_step) * qim_step
        
        # 调制：奇数倍 = 1, 偶数倍 = 0
        parity = int(np.round(quantized / qim_step)) % 2
        if parity != bit_val:
            if bit_val == 1:
                quantized += qim_step
            else:
                quantized -= qim_step
        
        dct_block[i, j] = quantized
        
        # 逆 DCT
        block_idct = idct(idct(dct_block.T, norm='ortho').T, norm='ortho')
        subband_pad[row*block_size:(row+1)*block_size,
                   col*block_size:(col+1)*block_size] = block_idct
        
        embedded_blocks += 1
        bit_idx += 1
    
    # 移除 padding
    subband_watermarked = subband_pad[:rows, :cols]
    
    # 逆 DWT
    coeffs_watermarked = (LL, (LH, subband_watermarked, HH))
    Y_watermarked = pywt.idwt2(coeffs_watermarked, 'haar')
    
    # 裁剪到原始尺寸
    Y_watermarked = Y_watermarked[:h, :w]
    
    # 合并通道
    ycrcb[:, :, 0] = np.clip(Y_watermarked, 0, 255).astype(np.uint8)
    result = cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)
    
    # 计算实际冗余度
    redundancy = embedded_blocks / total_bits_len
    print(f"[嵌入] 已写入 {embedded_blocks} 个块 (冗余度: {redundancy:.1f}x, 目标: {TARGET_REDUNDANCY}x)")
    
    return result


def extract_watermark(
    img: np.ndarray,
    watermark_key: str,
    qim_step: float = 40.0,
    var_threshold: float = None  # V4.2: 已废弃
) -> Tuple[Optional[WatermarkPayload], float]:
    """
    提取水印（支持 Multi-Scale Recovery + Adaptive Top-N）
    
    Args:
        img: BGR 图像
        watermark_key: 系统私钥
        qim_step: QIM 量化步长
        var_threshold: 已废弃（V4.2 使用 Top-N 策略）
    
    Returns:
        (WatermarkPayload 对象或 None, 置信度)
    """
    # 首先尝试原始尺寸提取
    payload, confidence = _extract_at_scale(img, watermark_key, qim_step)
    
    if payload is not None and confidence > 0.6:
        return payload, confidence
    
    # Multi-Scale Recovery：尝试多种缩放尺寸
    print("[提取] 原始尺寸提取失败，尝试 Multi-Scale Recovery...")
    target_sizes = [512, 768, 1024, 1280, 2048]
    
    for target_size in target_sizes:
        h, w = img.shape[:2]
        # 计算缩放比例（保持宽高比）
        scale = target_size / max(w, h)
        if abs(scale - 1.0) < 0.1:  # 跳过接近原始尺寸的
            continue
        
        new_w = int(w * scale)
        new_h = int(h * scale)
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
        
        payload_scaled, conf_scaled = _extract_at_scale(resized, watermark_key, qim_step)
        
        if payload_scaled is not None and conf_scaled > confidence:
            print(f"[提取] 在 {new_w}x{new_h} 尺寸下找到更好的结果 (置信度: {conf_scaled*100:.1f}%)")
            payload, confidence = payload_scaled, conf_scaled
            
            if confidence > 0.8:  # 提前退出
                break
    
    return payload, confidence


def _extract_at_scale(
    img: np.ndarray,
    watermark_key: str,
    qim_step: float
) -> Tuple[Optional[WatermarkPayload], float]:
    """
    在指定尺寸下提取水印（内部函数，使用 Top-N 策略）
    """
    h, w = img.shape[:2]
    
    # 转换色彩空间
    ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    Y = ycrcb[:, :, 0].astype(np.float32)
    
    # DWT 分解
    coeffs = pywt.dwt2(Y, 'haar')
    LL, (LH, HL, HH) = coeffs
    subband = HL
    
    # V5.0 安全缓冲池 + Top-N 块选择（必须与嵌入时完全一致）
    block_size = 8
    buffer_coords = _get_valid_blocks(subband, block_size)  # 返回 2x 缓冲池
    
    # 重建密钥驱动的随机序列
    seed = _generate_seed(watermark_key, w, h)
    rng = np.random.RandomState(seed)
    
    shuffled_coords = buffer_coords.copy()
    rng.shuffle(shuffled_coords)  # 必须与嵌入时完全一致
    
    # V5.0 关键：只使用前 MIN_BLOCKS 个块进行实际提取
    # 必须与嵌入时的 target_coords 完全一致
    target_coords = shuffled_coords[:MIN_BLOCKS]
    
    # Padding
    rows, cols = subband.shape
    rows_pad = (block_size - rows % block_size) % block_size
    cols_pad = (block_size - cols % block_size) % block_size
    subband_pad = np.pad(subband, ((0, rows_pad), (0, cols_pad)), mode='edge')
    
    # 提取比特流
    total_bits_len = PAYLOAD_BITS
    extracted_bits = []
    
    for row, col in target_coords:
        
        block = subband_pad[row*block_size:(row+1)*block_size,
                           col*block_size:(col+1)*block_size]
        
        dct_block = dct(dct(block.T, norm='ortho').T, norm='ortho')
        
        # 使用相同的随机位置选择
        mid_freq_positions = [(2, 1), (1, 2), (2, 2), (3, 1), (1, 3), (3, 2), (2, 3)]
        chosen_pos = mid_freq_positions[rng.randint(0, len(mid_freq_positions))]
        i, j = chosen_pos
        
        coeff = dct_block[i, j]
        quantized = np.round(coeff / qim_step) * qim_step
        parity = int(np.round(quantized / qim_step)) % 2
        
        extracted_bits.append(parity)
    
    if len(extracted_bits) < total_bits_len:
        return None, 0.0
    
    # 冗余投票（重塑为矩阵，列主序多数投票）
    # 每列代表一个比特位的多个副本
    num_copies = len(extracted_bits) // total_bits_len
    extracted_bits = extracted_bits[:num_copies * total_bits_len]  # 截断到整数倍
    
    bits_matrix = np.array(extracted_bits).reshape(num_copies, total_bits_len)
    voted_bits = np.round(np.mean(bits_matrix, axis=0)).astype(np.uint8)
    
    # 计算置信度
    confidence = min(1.0, num_copies / TARGET_REDUNDANCY)
    
    print(f"[提取] 检测到 {num_copies} 个副本，置信度: {confidence*100:.1f}%")
    
    # 反序列化
    try:
        payload_bytes = np.packbits(voted_bits).tobytes()
        payload = WatermarkPayload.from_bytes(payload_bytes)
        return payload, confidence
    except Exception as e:
        return None, 0.0
