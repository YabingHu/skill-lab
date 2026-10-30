#!/usr/bin/env python3
"""NE8000 BGP peer route-limit lab scenario; requires device_connector.py."""
import argparse
import datetime as dt
import ipaddress
import json
import os
import re
import uuid
from pathlib import Path

from device_connector import SSHConfig, execute_commands

BAD = re.compile(r"(?im)^\s*(?:Error:|%\s*Error|Unrecognized command|Incomplete command|Ambiguous command)")
COUNT = re.compile(r"Total Number of Routes\s*:\s*(\d+)", re.I)
PEER_ROW = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+4\s+\d+\s+.*?\s+(Established|Idle|Active|Connect|OpenSent|OpenConfirm)\s+\d+\s*$", re.I)
ACTIVE = {"limit_setting", "peer_announcing", "injected", "verify_incomplete", "recovery_started", "recovery_incomplete"}


def config(host):
    return SSHConfig(host=host, port=int(os.getenv("SSH_PORT", "22")),
                     username=os.environ["SSH_USERNAME"], password=os.getenv("SSH_PASSWORD"),
                     auth_model=os.getenv("AUTH_MODEL", "password"),
                     key_filename=os.getenv("KEY_FILENAME") or None)


def as_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, (tuple, list)):
        return "\n".join(as_text(v) for v in value)
    if isinstance(value, dict):
        for k in ("output", "stdout", "result", "results", "content"):
            if k in value:
                return as_text(value[k])
    for k in ("output", "stdout", "result", "results", "content"):
        if hasattr(value, k):
            return as_text(getattr(value, k))
    raise TypeError(f"Unsupported connector result: {type(value).__name__}")


def run(host, cmds):
    result = as_text(execute_commands(config(host), cmds))
    if not result.strip() or BAD.search(result):
        raise RuntimeError(f"CLI output invalid for {host}: {result[-1800:]!r}")
    return result


def save(path, data, new=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    if new:
        with path.open("x", encoding="utf-8") as f:
            f.write(payload)
    else:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)


def peer_state(text, peer):
    for line in text.splitlines():
        m = PEER_ROW.match(line)
        if m and m.group(1) == peer:
            return m.group(2).lower()
    return "unknown"


def peer_config(text, peer):
    lines = [l.strip() for l in text.splitlines()
             if re.match(r"^\s*peer\s+" + re.escape(peer) + r"\s+", l)]
    if not any(re.search(r"\bas-number\b", l) for l in lines):
        raise RuntimeError("Peer configuration was not found; stop")
    if any(re.search(r"\bgroup\b|\broute-limit\b", l, re.I) for l in lines):
        raise RuntimeError("Peer group or existing route-limit detected; refusing to overwrite inherited/present limits")
    return lines


def prepare(args):
    if args.host == args.peer_host:
        raise ValueError("local and remote SSH hosts must differ")
    peer = str(ipaddress.ip_address(args.peer))
    prefixes = [str(ipaddress.ip_network(p, strict=True)) for p in args.prefix]
    if len(set(prefixes)) != 2 or any(ipaddress.ip_network(p).prefixlen != 32 for p in prefixes):
        raise ValueError("Provide two different /32 lab-only routes")
    if any(ipaddress.ip_network(p).is_multicast or ipaddress.ip_network(p).is_loopback for p in prefixes):
        raise ValueError("Loopback/multicast experiment routes prohibited")
    local_out = run(args.host, ["display bgp peer"])
    if peer_state(local_out, peer) != "established":
        raise RuntimeError(f"Peer {peer} must initially be Established")
    local_cfg = run(args.host, ["display current-configuration configuration bgp"])
    peer_config(local_cfg, peer)
    remote_out = run(args.peer_host, ["display bgp peer"])
    remote_cfg = run(args.peer_host, ["display current-configuration configuration bgp"])
    routes = run(args.host, [f"display bgp routing-table peer {peer} received-routes"])
    match = COUNT.search(routes)
    if not match:
        raise RuntimeError("Cannot parse received route count; stop")
    n = int(match.group(1))
    if n < 1:
        raise RuntimeError("Unexpected zero received routes; stop")
    remote_static = run(args.peer_host, ["display current-configuration"])
    for prefix in prefixes:
        ip, plen = prefix.split("/")
        if re.search(r"(?m)^\s*ip route-static\s+" + re.escape(ip) + r"\s+" + plen + r"\b", remote_static):
            raise RuntimeError(f"Static route already exists: {prefix}")
        if re.search(r"(?m)^\s*network\s+" + re.escape(ip) + r"\s+" + plen + r"\b", remote_cfg):
            raise RuntimeError(f"BGP network already exists: {prefix}")
    return {
        "schema": 1, "scenario": "bgp-peer-route-limit", "status": "prepared",
        "run_id": uuid.uuid4().hex, "time": dt.datetime.now(dt.timezone.utc).isoformat(),
        "host": args.host, "peer_host": args.peer_host, "peer": peer, "asn": args.asn,
        "prefixes": prefixes, "original_route_count": n, "new_route_limit": n + 1,
        "original_peer_config": [x for x in local_cfg.splitlines() if re.search(r"^\s*peer\s+" + re.escape(peer) + r"\s+", x)],
        "before_local_bgp": local_out, "before_remote_bgp": remote_out,
        "before_local_config": local_cfg, "before_remote_config": remote_cfg,
        "before_received_routes": routes,
        "steps": {"local_limit": False, "remote_networks": False, "remote_static": False}
    }


def inject(args):
    state = prepare(args)
    print(f"Preflight: received={state['original_route_count']}, limit={state['new_route_limit']}, peer={state['peer']}")
    if not args.apply:
        print("DRY RUN: no configuration changes and no JSON created.")
        return
    if os.getenv("DEVICE_CONNECTOR_INTERACTIVE_OK") != "1":
        raise RuntimeError("Device connector has not been approved for interactive writes")
    path = Path(args.state)
    save(path, state, new=True)
    print(f"State file: {path.resolve()}")
    try:
        state["status"] = "limit_setting"
        save(path, state)
        run(args.host, ["system-view", f"bgp {args.asn}", f"peer {args.peer} route-limit {state['new_route_limit']}", "commit", "return"])
        state["steps"]["local_limit"] = True
        save(path, state)
        state["status"] = "peer_announcing"
        save(path, state)
        commands = ["system-view"]
        for prefix in state["prefixes"]:
            ip, plen = prefix.split("/")
            commands.append(f"ip route-static {ip} {plen} NULL 0")
        commands += ["commit", "return"]
        run(args.peer_host, commands)
        state["steps"]["remote_static"] = True
        save(path, state)
        commands = ["system-view", f"bgp {args.asn}", "ipv4-family unicast"]
        for prefix in state["prefixes"]:
            ip, plen = prefix.split("/")
            commands.append(f"network {ip} {plen}")
        commands += ["commit", "return"]
        run(args.peer_host, commands)
        state["steps"]["remote_networks"] = True
        save(path, state)
        output = run(args.host, ["display bgp peer"])
        state["after_local_bgp"] = output
        # A transient Established state is not enough to prove the limit triggered.
        state["status"] = "injected" if peer_state(output, args.peer) != "established" else "verify_incomplete"
        save(path, state)
        print(f"Current peer state: {peer_state(output,args.peer)}. Verify alarms/LastError manually. Status={state['status']}")
    except Exception:
        state["status"] = "verify_incomplete"
        save(path, state)
        raise


def recover(args):
    path = Path(args.state)
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("scenario") != "bgp-peer-route-limit" or state.get("host") != args.host or state.get("asn") != args.asn:
        raise RuntimeError("JSON does not match scenario, local host or ASN")
    if args.peer_host and args.peer_host != state["peer_host"]:
        raise RuntimeError("Remote SSH host does not match JSON")
    if not args.apply:
        print(f"DRY RUN recover: {state['prefixes']} / {state['peer']}")
        return
    if state["status"] == "restored":
        print("Already marked restored")
        return
    if os.getenv("DEVICE_CONNECTOR_INTERACTIVE_OK") != "1":
        raise RuntimeError("Connector write gate not enabled")
    # Preflight for drift: reject conflicting replacement configuration.
    cfg = run(state["host"], ["display current-configuration configuration bgp"])
    limits = re.findall(r"(?m)^\s*peer\s+" + re.escape(state["peer"]) + r"\s+route-limit\s+[^\r\n]+", cfg)
    expected = f"peer {state['peer']} route-limit {state['new_route_limit']}"
    if limits and any(l.strip() != expected for l in limits):
        raise RuntimeError("Current route-limit differs from this run; refusing to overwrite")
    state["status"] = "recovery_started"
    save(path, state)
    try:
        remote_cfg = run(state["peer_host"], ["display current-configuration configuration bgp"])
        for prefix in state["prefixes"]:
            ip, plen = prefix.split("/")
            if re.search(r"(?m)^\s*network\s+" + re.escape(ip) + r"\s+" + plen + r"\s*$", remote_cfg):
                run(state["peer_host"], ["system-view", f"bgp {state['asn']}", "ipv4-family unicast",
                                         f"undo network {ip} {plen}", "commit", "return"])
        state["steps"]["remote_networks"] = False
        save(path, state)
        local_cfg = run(state["host"], ["display current-configuration configuration bgp"])
        if re.search(r"(?m)^\s*" + re.escape(expected) + r"\s*$", local_cfg):
            run(state["host"], ["system-view", f"bgp {state['asn']}", f"undo peer {state['peer']} route-limit", "commit", "return"])
        state["steps"]["local_limit"] = False
        save(path, state)
        remote_all = run(state["peer_host"], ["display current-configuration"])
        for prefix in state["prefixes"]:
            ip, plen = prefix.split("/")
            pattern = r"(?m)^\s*ip route-static\s+" + re.escape(ip) + r"\s+" + plen + r"\s+NULL\s+0\s*$"
            if re.search(pattern, remote_all, re.I):
                run(state["peer_host"], ["system-view", f"undo ip route-static {ip} {plen} NULL 0", "commit", "return"])
        state["steps"]["remote_static"] = False
        save(path, state)
        local_after = run(state["host"], ["display bgp peer"])
        state["after_recovery_bgp"] = local_after
        state["status"] = "restored" if peer_state(local_after, state["peer"]) == "established" else "recovery_incomplete"
        save(path, state)
        print(f"Recovery commands executed. Peer={peer_state(local_after,state['peer'])}; status={state['status']}")
        if state["status"] != "restored":
            print("Inspect convergence; do not automatically reset a live peer.")
    except Exception:
        state["status"] = "recovery_incomplete"
        save(path, state)
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["inject", "recover"])
    p.add_argument("--host", required=True)
    p.add_argument("--peer-host")
    p.add_argument("--peer")
    p.add_argument("--asn", type=int, required=True)
    p.add_argument("--prefix", action="append", default=[])
    p.add_argument("--state", required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    if not os.getenv("SSH_USERNAME"):
        p.error("SSH_USERNAME not set")
    if args.action == "inject":
        if not args.peer_host or not args.peer or len(args.prefix) != 2:
            p.error("inject needs --peer-host, --peer and exactly two --prefix")
        inject(args)
    else:
        recover(args)


if __name__ == "__main__":
    main()
