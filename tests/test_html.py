from ecs_doctor._html import html_select_options


def test_html_select_options_escapes_cluster_names():
    html = html_select_options(
        ['prod"><script>alert(1)</script>', "payments"],
        placeholder="— Select a cluster —",
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "alert(1)" in html
    assert 'value="prod' in html
    assert "&quot;" in html or "&#x27;" in html or "&#34;" in html


def test_html_select_options_includes_placeholder_and_values():
    html = html_select_options(["svc-a", "svc-b"], placeholder="— Select a service —")
    assert "Select a service" in html
    assert 'value="svc-a"' in html
    assert ">svc-a</option>" in html
    assert 'value="svc-b"' in html
