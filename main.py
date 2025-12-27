import argparse
import sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Tuple
from tqdm import tqdm

from src.watermark_service import (
    load_config,
    create_master_copy,
    generate_distribution,
    check_watermark
)
from src.audit_service import generate_audit_report, batch_audit


def ensure_directories(config):
    """确保必要的目录存在"""
    Path(config['master_dir']).mkdir(parents=True, exist_ok=True)
    Path(config['dist_dir']).mkdir(parents=True, exist_ok=True)
    Path(config['input_dir']).mkdir(parents=True, exist_ok=True)
    print(f"[系统] 目录检查完成: {config['master_dir']}, {config['dist_dir']}")


def _process_single_master(args: Tuple[str, dict]) -> Tuple[str, bool, str]:
    """
    处理单个母带制作任务（用于并发）
    
    Args:
        args: (file_path, config) 元组
    
    Returns:
        (file_path, success, message) 元组
    """
    file_path, config = args
    try:
        output_path = create_master_copy(file_path, config)
        return (file_path, True, output_path)
    except Exception as e:
        return (file_path, False, str(e))


def cmd_sign(args, config):
    """批量母带制作"""
    print("\n" + "=" * 60)
    print("SGP 1.0 - 母带制作 (Master Copy Creation)")
    print("=" * 60)
    
    input_dir = Path(config['input_dir'])
    if not input_dir.exists():
        print(f"[错误] 输入目录不存在: {input_dir}")
        return 1
    
    # 查找所有图像文件
    image_extensions = {'.png', '.jpg', '.jpeg', '.bmp'}
    image_files = [f for f in input_dir.iterdir() 
                   if f.is_file() and f.suffix.lower() in image_extensions]
    
    if not image_files:
        print(f"[错误] 在 {input_dir} 中未找到图像文件")
        return 1
    
    print(f"[系统] 找到 {len(image_files)} 个图像文件")
    print(f"[系统] 并发进程数: {config['workers']}")
    
    # 准备任务列表
    tasks = [(str(f), config) for f in image_files]
    
    # 并发处理
    success_count = 0
    fail_count = 0
    
    if len(tasks) == 1:
        # 单文件直接处理
        file_path, success, message = _process_single_master(tasks[0])
        if success:
            success_count = 1
            print(f"\n[成功] {Path(file_path).name} -> {message}")
        else:
            fail_count = 1
            print(f"\n[失败] {Path(file_path).name}: {message}")
    else:
        # 多文件并发处理
        with ProcessPoolExecutor(max_workers=config['workers']) as executor:
            future_to_file = {executor.submit(_process_single_master, task): task[0] 
                            for task in tasks}
            
            with tqdm(total=len(tasks), desc="[母带制作]", unit="file") as pbar:
                for future in as_completed(future_to_file):
                    file_path, success, message = future.result()
                    if success:
                        success_count += 1
                        pbar.write(f"✅ {Path(file_path).name}")
                    else:
                        fail_count += 1
                        pbar.write(f"❌ {Path(file_path).name}: {message}")
                    pbar.update(1)
    
    # 统计摘要
    print("\n" + "=" * 60)
    print(f"批量处理完成: {success_count} 成功, {fail_count} 失败")
    print("=" * 60)
    
    return 0 if fail_count == 0 else 1


def cmd_distribute(args, config):
    """生成分发版本"""
    print("\n" + "=" * 60)
    print("SGP 1.0 - 分发生成 (Distribution Generation)")
    print("=" * 60)
    
    try:
        output_path = generate_distribution(args.file, args.user, config)
        print(f"\n[成功] 分发版本已生成: {output_path}")
        return 0
    except Exception as e:
        print(f"\n[错误] 分发生成失败: {e}")
        return 1


def cmd_check(args, config):
    """检查水印信息"""
    print("\n" + "=" * 60)
    print("SGP 1.0 - 水印检查 (Watermark Verification)")
    print("=" * 60)
    
    if args.file:
        # 单文件检查
        try:
            result = check_watermark(args.file, config)
            
            # 默认生成详细审计报告
            report_path = generate_audit_report(args.file, config)
            print(f"\n[报告] 详细审计报告已保存: {report_path}")
            
            return 0
        except Exception as e:
            print(f"\n[错误] 检查失败: {e}")
            return 1
    elif args.batch:
        # 批量检查
        try:
            stats = batch_audit(args.batch, config)
            return 0
        except Exception as e:
            print(f"\n[错误] 批量检查失败: {e}")
            return 1
    else:
        # 默认检查所有母带和分发文件
        print("[系统] 检查所有母带和分发文件...")
        
        master_dir = Path(config['master_dir'])
        dist_dir = Path(config['dist_dir'])
        
        all_files = []
        if master_dir.exists():
            all_files.extend(master_dir.glob('*.png'))
            all_files.extend(master_dir.glob('*.jpg'))
        if dist_dir.exists():
            all_files.extend(dist_dir.glob('*.png'))
            all_files.extend(dist_dir.glob('*.jpg'))
        
        if not all_files:
            print("[提示] 未找到任何文件")
            return 0
        
        print(f"[系统] 找到 {len(all_files)} 个文件，正在生成审计报告...\n")
        
        for file_path in all_files:
            try:
                check_watermark(str(file_path), config)
                # 为每个文件生成审计报告
                report_path = generate_audit_report(str(file_path), config)
                print(f"[报告] 已保存: {report_path}\n")
            except Exception as e:
                print(f"[错误] {file_path.name}: {e}\n")
        
        print(f"[完成] 所有审计报告已生成到 output_reports/ 目录")
        
        return 0


def main():
    """主程序入口"""
    parser = argparse.ArgumentParser(
        description='SGP 1.0 - ShadowGuard Protocol (Enterprise DRM)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 批量制作母带
  python main.py sign
  
  # 生成分发版本
  python main.py distribute -f 可儿_master.png -u 987654321098765432109876
  
  # 检查水印信息
  python main.py check
  python main.py check -f storage/masters/可儿_master.png
  python main.py check -f storage/masters/可儿_master.png --report
  python main.py check --batch storage/distribution
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='可用命令')
    
    # sign 命令
    parser_sign = subparsers.add_parser('sign', help='批量制作母带')
    
    # distribute 命令
    parser_dist = subparsers.add_parser('distribute', help='生成分发版本')
    parser_dist.add_argument('-f', '--file', required=True, 
                            help='母带文件名（例如：可儿_master.png）')
    parser_dist.add_argument('-u', '--user', type=int, required=True,
                            help='用户 UID（25 位数字）')
    
    # check 命令
    parser_check = subparsers.add_parser('check', help='检查水印信息')
    parser_check.add_argument('-f', '--file', help='单个文件路径')
    parser_check.add_argument('--batch', help='批量检查目录')
    parser_check.add_argument('--report', action='store_true', 
                             help='生成详细审计报告')
    
    args = parser.parse_args()
    
    # 加载配置
    try:
        config = load_config()
        print(f"[系统] 配置加载成功")
        print(f"[系统] 作者 UID: {config['owner_uuid']}")
        print(f"[系统] 水印密钥: {config['watermark_key'][:8]}..." )
    except Exception as e:
        print(f"[错误] 配置加载失败: {e}")
        return 1
    
    # 确保目录存在
    ensure_directories(config)
    
    # 路由命令
    if args.command == 'sign':
        return cmd_sign(args, config)
    elif args.command == 'distribute':
        return cmd_distribute(args, config)
    elif args.command == 'check':
        return cmd_check(args, config)
    else:
        parser.print_help()
        return 0


if __name__ == '__main__':
    sys.exit(main())
