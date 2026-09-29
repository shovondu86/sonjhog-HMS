"""Compares the running app's generated OpenAPI against hospital-api-v1_2_openapi.yaml:
paths, methods, operationIds, parameter names/locations, request-body required fields."""
import os, sys
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:///./contract_check.db")
from app.main import app  # noqa: E402

spec = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "hospital-api-v1_2_openapi.yaml")))
gen = app.openapi()
METHODS = {"get", "post", "put", "delete", "patch"}


def resolve(doc, obj):
    while isinstance(obj, dict) and "$ref" in obj:
        node = doc
        for part in obj["$ref"].lstrip("#/").split("/"):
            node = node[part]
        obj = node
    return obj


def params(doc, path_item, op):
    out = {}
    for p in list(path_item.get("parameters", [])) + list(op.get("parameters", [])):
        p = resolve(doc, p)
        out[(p["in"], p["name"].lower())] = bool(p.get("required"))
    return out


def body_required(doc, op):
    rb = op.get("requestBody")
    if not rb:
        return None
    rb = resolve(doc, rb)
    for mt, v in rb.get("content", {}).items():
        sch = resolve(doc, v.get("schema", {}))
        req = set(sch.get("required", []))
        for sub in sch.get("allOf", []):
            req |= set(resolve(doc, sub).get("required", []))
        return mt, req
    return None


problems = []
sp, gp = spec["paths"], gen["paths"]
for path, item in sp.items():
    if path not in gp:
        problems.append(f"MISSING PATH {path}")
        continue
    for m in METHODS & set(item):
        if m not in gp[path]:
            problems.append(f"MISSING {m.upper()} {path}")
            continue
        so, go = item[m], gp[path][m]
        if so.get("operationId") != go.get("operationId"):
            problems.append(f"operationId {m.upper()} {path}: spec={so.get('operationId')} app={go.get('operationId')}")
        sps, gps = params(spec, item, so), params(gen, gp[path], go)
        for k, req in sps.items():
            if k not in gps:
                problems.append(f"MISSING PARAM {m.upper()} {path} {k}")
            elif req and not gps[k]:
                problems.append(f"PARAM not required {m.upper()} {path} {k}")
        for k in gps:
            if k not in sps and k[0] != "cookie":
                problems.append(f"EXTRA PARAM {m.upper()} {path} {k}")
        sb, gb = body_required(spec, so), body_required(gen, go)
        if (sb is None) != (gb is None):
            problems.append(f"BODY presence differs {m.upper()} {path}")
        elif sb:
            if sb[0] != gb[0]:
                problems.append(f"BODY media type {m.upper()} {path}: {sb[0]} vs {gb[0]}")
            if sb[1] - gb[1]:
                problems.append(f"BODY required fields missing {m.upper()} {path}: {sb[1] - gb[1]}")
        for code in so.get("responses", {}):
            if str(code) not in go.get("responses", {}) and code not in go.get("responses", {}):
                problems.append(f"undocumented response {code} on {m.upper()} {path}")
for path in gp:
    if path not in sp:
        problems.append(f"EXTRA PATH {path}")

# schema property names
ss, gs = spec["components"]["schemas"], gen["components"]["schemas"]


def props(doc, sch, seen=0):
    sch = resolve(doc, sch)
    out = set(sch.get("properties", {}))
    for sub in sch.get("allOf", []):
        out |= props(doc, sub)
    return out


for name, sch in ss.items():
    if name in ("Body_login_auth_login_post", "HTTPValidationError", "ValidationError"):
        continue
    if name not in gs:
        problems.append(f"MISSING SCHEMA {name}")
        continue
    a, b = props(spec, sch), props(gen, gs[name])
    if a - b:
        problems.append(f"SCHEMA {name} missing props {sorted(a - b)}")
    if b - a:
        problems.append(f"SCHEMA {name} extra props {sorted(b - a)}")

print(f"spec paths={len(sp)} ops={sum(len(METHODS & set(i)) for i in sp.values())}  app paths={len(gp)}")
print("\n".join(problems) if problems else "CONTRACT OK — no differences found")
