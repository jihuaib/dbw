"""完整 CLI 语法匹配：嵌套可选项、必选分支和中间参数。"""
from app.modules.kb import syntax as S


def test_analyze_renders_safe_minimum_or_required_prefix():
    optional = S.analyze(
        "display bgp [ instance <instance-name> ] all peer summary")
    assert optional["command"] == "display bgp all peer summary"
    assert optional["required"] == []
    assert optional["params"] == ["instance-name"]

    required = S.analyze(
        "display bgp bmp server <server-number> monitor-peer all")
    assert required["command"] == "display bgp bmp server"
    assert required["required"] == ["server-number"]


def test_match_optional_middle_parameter_and_fixed_suffix():
    syntax = "display bgp [ instance <instance-name> ] all peer summary"
    assert S.match(syntax, "display bgp all peer summary").command == \
        "display bgp all peer summary"
    assert S.match(syntax, "display bgp instance Blue all peer summary").command == \
        "display bgp instance Blue all peer summary"
    assert S.match(syntax, "display bgp instance all peer summary") is None


def test_match_required_choice_and_parameter():
    syntax = (
        "show bgp route af {ipv4-qp|ipv6-qp} peer "
        "<ipv4-address|ipv6-address> advertise-routes [<qp-route-key>]")
    command = ("show bgp route af ipv4-qp peer 10.0.0.1 advertise-routes "
               "dqpn=1,ip=10.0.0.0/24")
    assert S.match(syntax, command).command == command
    assert S.match(
        syntax, "show bgp route af ipv8-qp peer 10.0.0.1 advertise-routes") is None
    assert S.match(
        syntax, "show bgp route af ipv4-qp peer advertise-routes") is None


def test_match_nested_optional_and_preserves_parameter_case():
    syntax = "display ospf [<process-id>] peer [interface [<if-name>]]"
    assert S.match(syntax, "display ospf peer").command == "display ospf peer"
    got = S.match(syntax, "display ospf 1 peer interface GE-1")
    assert got.command == "display ospf 1 peer interface GE-1"


def test_match_is_anchored_and_rejects_injection_before_folding():
    syntax = "show bgp summary ..."
    assert S.match(syntax, "show bgp summary")
    assert S.match(syntax, "show bgp summary unknown") is None
    assert S.match(syntax, "show bgp summary\nreboot") is None
    assert S.match(syntax, "show bgp summary; reboot") is None


def test_parser_rejects_empty_branches_invalid_params_and_excessive_nesting():
    invalid = [
        "show bgp { | reset }",
        "show bgp [ ]",
        "show bgp <peer name>",
        "show bgp " + ("[" * 65) + "summary" + ("]" * 65),
    ]
    for syntax in invalid:
        try:
            S.parse(syntax)
        except ValueError:
            continue
        raise AssertionError("invalid syntax accepted: " + syntax)


def test_repeated_parameters_are_bounded_and_fully_consumed():
    syntax = "display bgp community <community-number>&<1-3>"
    assert S.analyze(syntax)["required"] == ["community-number"]
    assert S.match(syntax, "display bgp community 100")
    assert S.match(syntax, "display bgp community 100 200 300")
    assert S.match(syntax, "display bgp community") is None
    assert S.match(syntax, "display bgp community 1 2 3 4") is None


def test_ls_prefix_allows_only_balanced_bracket_encoding():
    syntax = "display bgp link-state <ls-prefix> segment-list"
    prefix = (
        "[E][B][I0x0][N[r1.1.1.2]][c65008]"
        "[R[r44.33.22.11]][c65009][L[i2.1.1.3][n1.1.1.3]]/536")
    command = "display bgp link-state " + prefix + " segment-list"
    assert S.match(syntax, command).command == command
    assert S.match(syntax, "display bgp link-state foo segment-list") is None
    assert S.match(syntax, "display bgp link-state [E][B segment-list") is None
