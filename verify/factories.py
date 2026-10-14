"""Factory flag: a launch whose creator is one of the listed high-volume pump.fun deployers, or whose creation signer
was funded by one of them or sent SOL to one. Descriptive fact with its source; whether such launches lose money for
an outside buyer is a separate, pre-registered test. The operator's list is not published in this repository:
factories.deployers is empty here and is set in config.local.yaml."""


def listed(cfg):
    f = cfg.get("factories") or {}
    return set(f.get("deployers") or [])


def check(cfg, signer, curve_creator, signer_funders=(), signer_recipients=()):
    lst = listed(cfg)
    f = cfg.get("factories") or {}
    roles = []
    if signer and signer in lst:
        roles.append("creation_signer")
    if curve_creator and curve_creator in lst and curve_creator != signer:
        roles.append("curve_creator")
    funded_by = sorted(set(signer_funders) & lst)
    sent_to = sorted(set(signer_recipients) & lst)
    if funded_by:
        roles.append("signer_funded_by_listed")
    if sent_to:
        roles.append("signer_sent_to_listed")
    return {"flagged": bool(roles), "roles": roles, "funded_by": funded_by, "sent_to": sent_to,
            "list_size": len(lst), "source": f.get("source"), "ev_status": f.get("ev_status")}
