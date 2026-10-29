"""Fixed-code diagnostics do not reveal model output or weaken the parser."""
import pytest

from guance_agent.business.organize.diagnostics import parse_failure_reason
from guance_agent.business.organize.synthesis import parse_output


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("", "empty_output"),
        ("[]", "root_not_object"),
        ("not JSON", "no_json_object"),
        ('[{"policies":[]}]', "unexpected_wrapper"),
        ('{"policies":[', "incomplete_json"),
        ('{"policies":"not a list"}', "invalid_policies"),
        ('{"topic":"x"}', "missing_policies"),
        ('{"policies":[]} {"policies":[]}', "multiple_objects"),
        ('{"policies":[]} ]', "unexpected_wrapper"),
    ],
)
def test_diagnostic_codes_for_rejected_outputs(raw, reason):
    with pytest.raises(ValueError):
        parse_output(raw)
    assert parse_failure_reason(raw) == reason


def test_valid_single_object_is_still_accepted():
    assert parse_output('{"policies": []}') == {"policies": []}


def test_unterminated_string_is_classified_without_logging_content():
    raw = '{"policies":[{"planid":"20699'
    with pytest.raises(ValueError):
        parse_output(raw)
    assert parse_failure_reason(raw) == "incomplete_json"
