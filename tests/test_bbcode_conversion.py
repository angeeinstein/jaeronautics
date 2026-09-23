"""Turning a decade of BBCode into something readable.

Frequencies from the real export: color 484, size 434, b 428, attachment 145,
u 104, list 78, quote 62, i 42, font 38, hr 37, url 32, align 17.

Colour, size, font and alignment are dropped to their contents on purpose.
They are somebody's 2014 formatting choices rather than information, and
inventing HTML to keep them would make every imported post a special case for
ever. Losing the words would be a different matter, so these mostly check that
the text survives whatever happens to the markup around it.
"""
import pytest

from aeronautics_members.services.forum_content import bbcode_to_markdown


class TestTheMarkupWithAnEquivalent:
    @pytest.mark.parametrize("source,expected", [
        ("[b]Klausur[/b]", "**Klausur**"),
        ("[i]circa[/i]", "*circa*"),
        ("[s]alt[/s]", "~~alt~~"),
        ("[url=https://example.at]Angabe[/url]", "[Angabe](https://example.at)"),
        ("[url]https://example.at[/url]", "https://example.at"),
        ("[img]https://example.at/a.png[/img]", "![](https://example.at/a.png)"),
    ])
    def test_it_becomes_markdown(self, source, expected):
        assert bbcode_to_markdown(source) == expected

    def test_a_list_becomes_a_list(self):
        result = bbcode_to_markdown("[list][*]Angabe[*]Lösung[*]Formelsammlung[/list]")

        assert result.splitlines() == ["- Angabe", "- Lösung", "- Formelsammlung"]

    def test_code_keeps_its_shape(self):
        result = bbcode_to_markdown("[code]int main() {\n  return 0;\n}[/code]")

        assert "```" in result
        assert "int main()" in result


class TestQuoting:
    def test_a_quote_becomes_a_blockquote(self):
        result = bbcode_to_markdown("[quote]Wo finde ich die Angabe?[/quote]")

        assert "> Wo finde ich die Angabe?" in result

    def test_the_person_quoted_is_kept(self):
        result = bbcode_to_markdown("[quote=SpaniolA_L18]Danke![/quote]")

        assert "SpaniolA_L18" in result
        assert "> Danke!" in result

    def test_a_quote_inside_a_quote_unwraps(self):
        """Nested quotes are how a thread argues with itself."""
        result = bbcode_to_markdown(
            "[quote=A][quote=B]erste Frage[/quote]meine Antwort[/quote]"
        )

        assert "erste Frage" in result
        assert "meine Antwort" in result
        assert "[quote" not in result


class TestTheMarkupWithoutOne:
    @pytest.mark.parametrize("source", [
        "[color=#FF0000]Achtung[/color]",
        "[size=4]Achtung[/size]",
        "[font=Arial]Achtung[/font]",
        "[align=center]Achtung[/align]",
        "[u]Achtung[/u]",
    ])
    def test_the_words_survive_even_though_the_styling_does_not(self, source):
        result = bbcode_to_markdown(source)

        assert result == "Achtung"
        assert "[" not in result

    def test_nested_decoration_unwraps_completely(self):
        """Real posts stack these three or four deep."""
        result = bbcode_to_markdown(
            "[align=center][size=4][color=#0000FF][b]Klausur 2019[/b][/color][/size][/align]"
        )

        assert result == "**Klausur 2019**"


class TestItDoesNotMangleOrdinaryText:
    def test_german_text_is_untouched(self):
        source = "Grüße aus Graz — die Lösung hängt an. Straße, Maße, Öl."

        assert bbcode_to_markdown(source) == source

    def test_something_that_only_looks_like_bbcode_is_left_alone(self):
        source = "siehe Aufgabe [3] und [4]"

        assert bbcode_to_markdown(source) == source

    def test_an_empty_post_stays_empty(self):
        assert bbcode_to_markdown("") == ""
        assert bbcode_to_markdown(None) == ""

    def test_an_inline_attachment_reference_is_left_visible(self):
        """145 of these across the whole board.

        Guessing which upload each one meant would be a silent wrong answer;
        leaving it shows up when somebody reads the post.
        """
        result = bbcode_to_markdown("Angabe: [attachment=12]")

        assert "[attachment=12]" in result


class TestWhatTheRealExportActuallyContains:
    """Both of these were found by running the converter over all 1,529 posts.

    Neither showed up against invented examples, because invented examples are
    always well-formed and always use the documented syntax.
    """

    def test_mybbs_real_quote_header_is_read_properly(self):
        """MyBB does not write [quote=Name].

        It writes [quote='Name' pid='112' dateline='1394119460'], and taking
        everything after the "=" put a post id and a unix timestamp into the
        attribution line of every quoted post on the board.
        """
        result = bbcode_to_markdown(
            "[quote='BeemsterJ_M13' pid='112' dateline='1394119460']\nHay Boys[/quote]"
        )

        assert "**BeemsterJ_M13**" in result
        assert "pid=" not in result
        assert "dateline" not in result
        assert "1394119460" not in result

    @pytest.mark.parametrize("source,expected", [
        ("[size=4]Klausur", "Klausur"),
        ("[color=#FF0000]Achtung", "Achtung"),
        ("Angabe[/size]", "Angabe"),
        ("[b]Lösung", "Lösung"),
    ])
    def test_a_tag_that_was_never_closed_is_removed(self, source, expected):
        """A decade of posts contains plenty. The paired passes cannot see them."""
        assert bbcode_to_markdown(source) == expected

    @pytest.mark.parametrize("source", [
        "Frage an [LAV19] zur Angabe",
        "siehe Protokoll [20140326]",
        "auf [facebook] gepostet",
    ])
    def test_ordinary_words_in_brackets_are_not_markup(self, source):
        """All three appear in the real export.

        Stripping anything inside square brackets would quietly edit what
        people wrote, which is worse than leaving a stray tag.
        """
        assert bbcode_to_markdown(source) == source
