#!/usr/bin/env python3
"""BGP Router-ID 冲突实验：仅通过 device_connector.execute_commands 操作设备。

约定：execute_commands(config, commands) 返回可提取的命令文本；且对写入命令支持
同一会话中连续执行，并由连接器内部处理 router-id 后的 [Y/N] 交互。若不满足，写操作前不要启用。
"""
import argparse
import datetime as dt
import ipaddress
import json
import logging
import os
import re
from pathlib import Path

from device_connector import SSHConfig, execute_commands

logger = logging.getLogger(__name__)
RID_RE = re.compile(r'BGP\s+local\s+router\s+ID\s*:\s*(\d+\.\d+\.\d+\.\d+)', re.I)
AS_RE = re.compile(r'Local\s+AS\s+number\s*:\s*(\d+)', re.I)
CONF_RID_RE = re.compile(r'^\s*router-id\s+(\d+\.\d+\.\d+\.\d+)\s*$', re.M)
BAD_RE = re.compile(r'(?im)^\s*(?:Error:|%\s*Error|Unrecognized command|Incomplete command|Ambiguous command)')
STATES = {'established', 'active', 'connect', 'idle', 'opensent', 'openconfirm'}


def config_for(host):
    return SSHConfig(
        host=host,
        port=int(os.getenv('SSH_PORT', '22')),
        username=os.environ['SSH_USERNAME'],
        password=os.getenv('SSH_PASSWORD'),
        auth_model=os.getenv('AUTH_MODEL', 'password'),
        key_filename=os.getenv('KEY_FILENAME') or None,
    )


def output_text(value):
    """适配常见返回结构；不能读取的返回值报错，不以空字符串假装成功。"""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if isinstance(value, (list, tuple)):
        return '\n'.join(output_text(x) for x in value)
    if isinstance(value, dict):
        for name in ('output', 'stdout', 'result', 'results', 'data', 'content'):
            if name in value:
                return output_text(value[name])
    for name in ('output', 'stdout', 'result', 'results', 'content'):
        if hasattr(value, name):
            return output_text(getattr(value, name))
    raise TypeError(f'无法识别 execute_commands 返回类型: {type(value).__name__}，请适配 output_text()')


def run(host, commands):
    logger.info('执行 %s: %s', host, commands)
    response = execute_commands(config_for(host), commands)
    output = output_text(response)
    if not output.strip():
        raise RuntimeError('命令无可解析回显，无法继续：检查 execute_commands 是否返回文本')
    if BAD_RE.search(output):
        raise RuntimeError(f'{host} 回显包含命令错误: {output[-1200:]}')
    return output


def inspect(host, expected_as):
    out = run(host, ['display bgp peer'])
    rid, asn = RID_RE.search(out), AS_RE.search(out)
    if not rid or not asn:
        raise RuntimeError(f'{host} 的 BGP Router-ID/AS 无法解析')
    if int(asn.group(1)) != expected_as:
        raise RuntimeError(f'{host} AS 不匹配: {asn.group(1)} != {expected_as}')
    peers = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 8 or parts[1] != '4':
            continue
        try:
            ip = str(ipaddress.IPv4Address(parts[0]))
            peer_as = int(parts[2])
        except (ValueError, IndexError):
            continue
        state = parts[-2].lower() if parts[-1].isdigit() else parts[-1].lower()
        if state in STATES:
            peers.append({'ip': ip, 'as': peer_as, 'state': state})
    return rid.group(1), peers, out


def backup_write(path, state):
    """Create a unique backup, never overwrite an existing fault run.

    Return the actual file path so every later state update uses the same file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        # Do not silently run another injection over a potentially active fault.
        previous = json.loads(path.read_text(encoding='utf-8'))
        unresolved = {
            'apply_started', 'injected', 'verify_failed',
            'recovery_started', 'recovery_failed',
        }
        if previous.get('host') == state.get('host') and previous.get('status') in unresolved:
            raise RuntimeError(
                f'已有未确认恢复的故障记录: {path} ({previous.get("status")})；'
                '先核对设备并恢复，再继续注入'
            )
        stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        path = path.with_name(f'{path.stem}_{stamp}{path.suffix}')

    # Exclusive create also avoids accidental overwrites on timestamp collisions.
    with path.open('x', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    logger.info('备份状态文件: %s', path)
    return path


def update_state(path, state):
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


def confirm(args, msg):
    logger.warning(msg)
    if not args.apply:
        logger.info('预演模式：未修改配置；需加 --apply')
        return False
    if os.getenv('DEVICE_CONNECTOR_INTERACTIVE_OK') != '1':
        raise RuntimeError(
            '写入操作要求事先验证 execute_commands 支持会话保持和 [Y/N] 应答，'
            '并设置 DEVICE_CONNECTOR_INTERACTIVE_OK=1'
        )
    logger.info('已启用 --apply，跳过人工输入确认')
    return True


def change_router_id(args, rid):
    """使用连接器内置的交互确认机制；不要把 y 作为独立命令传入。"""
    commands = [
        'system-view',
        f'bgp {args.asn}',
        f'router-id {rid}',
        'commit',
        'return',
    ]
    # execute_commands 必须自动回应 router-id 变更的 [Y/N]，否则可能提交失败。
    out = run(args.host, commands)
    if re.search(r'Continue\?\s*\[Y/N\]', out, re.I):
        logger.info('设备返回 Router-ID 变更确认提示，请确认连接器已自动应答')
    if not re.search(r'(?im)\bcommit\s+(?:complete|succeeded|successfully)|\bcommit\b', out):
        logger.warning('回显中未明确确认 commit 结果，将依赖读回校验')
    return out


def inject(args):
    old, peers, before = inspect(args.host, args.asn)
    subnet = ipaddress.ip_network(args.peer_subnet, strict=False)
    candidates = [p for p in peers if p['state'] == 'established' and
                  ipaddress.ip_address(p['ip']) in subnet and p['ip'] != args.host]
    if args.peer:
        candidates = [p for p in candidates if p['ip'] == args.peer]
    if len(candidates) != 1:
        raise RuntimeError('候选 Peer 必须恰好一个，使用 --peer 指定：' + ', '.join(p['ip'] for p in candidates))
    peer = candidates[0]['ip']
    target_rid, _, peer_out = inspect(peer, args.asn)
    if old == target_rid:
        raise RuntimeError('本端与对端 Router-ID 已相同，停止注入')
    # 一次调用完成 system-view、bgp视图、display this；依赖 execute_commands 保持会话。
    cfg_output = run(args.host, ['system-view', f'bgp {args.asn}', 'display this', 'return'])
    m = CONF_RID_RE.search(cfg_output)
    if not m or m.group(1) != old:
        raise RuntimeError('原始 router-id 配置缺失或与运行值不符，不允许自动注入')
    path = Path(args.state)
    state = {'time': dt.datetime.now(dt.timezone.utc).isoformat(), 'status': 'prepared',
             'host': args.host, 'asn': args.asn, 'peer': peer, 'original_router_id': old,
             'injected_router_id': target_rid, 'peer_subnet': args.peer_subnet,
             'before_bgp_peer': before, 'peer_before': peer_out, 'original_bgp_config': cfg_output}
    path = backup_write(path, state)
    print(f'原 Router-ID={old}，对端 {peer} Router-ID={target_rid}，备份文件={path}')
    if not confirm(args, '修改 Router-ID 将重置本端 BGP 邻居，可能影响业务。'):
        return
    state['status'] = 'apply_started'
    update_state(path, state)
    change_router_id(args, target_rid)
    actual, peers_after, out = inspect(args.host, args.asn)
    state.update(status='injected' if actual == target_rid else 'verify_failed', after_bgp_peer=out)
    update_state(path, state)
    if actual != target_rid:
        raise RuntimeError('注入值读回不一致，请按状态文件恢复并人工核查')
    print(f'配置注入成功，目标 Peer 状态：{next((p["state"] for p in peers_after if p["ip"] == peer), "missing")}')
    print('仍需在收敛观察窗口内核对会话持续异常，单纯 Router-ID 相同不代表故障已生效')


def recover(args):
    path = Path(args.state)
    state = json.loads(path.read_text(encoding='utf-8'))
    if state['host'] != args.host or state['asn'] != args.asn:
        raise RuntimeError('备份文件与设备/AS 不匹配')
    old, injected = state['original_router_id'], state['injected_router_id']
    current, peers, _ = inspect(args.host, args.asn)
    if current == old:
        print('Router-ID 已是原值；仍须核查邻居与路由恢复情况')
        return
    if current != injected:
        raise RuntimeError(f'当前 Router-ID={current}，非注入记录值={injected}，拒绝覆盖')
    if not confirm(args, f'恢复 BGP Router-ID：{current} -> {old}，将再次重置邻居'):
        return
    state['status'] = 'recovery_started'
    update_state(path, state)
    change_router_id(args, old)
    actual, peers_after, out = inspect(args.host, args.asn)
    state.update(status='router_id_restored' if actual == old else 'recovery_failed', recovery_bgp_peer=out)
    update_state(path, state)
    if actual != old:
        raise RuntimeError('恢复读回验证失败，请人工检查')
    target_state = next((p['state'] for p in peers_after if p['ip'] == state['peer']), 'missing')
    print(f'Router-ID 恢复到 {old}，目标 Peer 当前状态 {target_state}；需等待收敛并检查路由')


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inject', 'recover'])
    parser.add_argument('--host', default='10.118.192.167')
    parser.add_argument('--asn', type=int, default=100)
    parser.add_argument('--peer', default='10.118.192.166')
    parser.add_argument('--peer-subnet', default='10.118.192.0/24')
    parser.add_argument('--state', default='bgp_router_id_state.json')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not os.getenv('SSH_USERNAME'):
        parser.error('必须配置 SSH_USERNAME 环境变量')
    getattr(__import__(__name__), args.action)(args)


if __name__ == '__main__':
    main()
