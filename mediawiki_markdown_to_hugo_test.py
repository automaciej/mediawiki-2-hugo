import mediawiki_markdown_to_hugo as m

import logging
import unittest

from pathlib import Path

TEST_ARTICLE_1 = """
word

[web link](https://www.example.com "web link title")

[kategoria:technika gry](kategoria:technika_gry "wikilink")

[Another article](Another_article "wikilink")
"""

TEST_ARTICLE_2 = """

**Artykulacja** jest jednym z najważniejszych elementów muzycznych.
Często decyduje o tym, czy daną muzykę możemy nazwać jazzem czy nie.
Artykulacja jazzowa różni się zasadniczo od artykulacji stosowanej w
klasycznej muzyce europejskiej, bowiem inne jest podejście muzyka do
dźwięku i jego przebiegu. Do każdej improwizowanej frazy jazzman ma
stosunek bardziej osobisty - on jest przecież jej twórca w całym
znaczeniu tego słowa. Ta jedność kompozytora i wykonawcy skupiająca się
w osobie muzyka jazzowego, pozwala pominąć niedoskonały i nie oddający
wiernie intencji kompozytora zapis nutowy. Zresztą muzyk jazzowy grając
z nut, też ma ogromną swobodę, która wykracza ponad znaczenie słowa
„interpretacja". W technice gry, metody artykulacji zbliżone są do
metod, jakimi posługuje się muzyk wychowany w tradycji muzyki
europejskiej.

W tym rozdziale umieszczę trzy bardzo ważne elementy artykulacji:
[legato](legato "wikilink"), [staccato](staccato "wikilink") i
[akcentowanie](akcentowanie "wikilink").

  - [Legato](Legato "wikilink")
  - [Akcentowanie](Akcentowanie "wikilink")
  - [Ozdobniki](Ozdobniki "wikilink")
  - [Flażolety](Flażolet "wikilink")

[kategoria:technika gry](kategoria:technika_gry "wikilink")
[kategoria:inna kategoria](kategoria:inna_kategoria "wikilink")
"""

class ConversionTest(unittest.TestCase):

  def testBasicStuff(self):
    doc = m.Document(TEST_ARTICLE_1,
                     Path("bar/Test_Article_1.md"), None)
    self.assertEqual("/bar/test-article-1", doc.URLPath())
    fm = doc.fm
    self.assertEqual(fm.title, "Test Article 1")
    self.assertEqual(fm.slug, "test-article-1")

  def testTitleFromPath(self):
    self.assertEqual(
      "Szła dzieweczka do laseczka",
      m.TitleFromPath("content/książka/Szła_dzieweczka_do_laseczka.md"))

  def testTitleFromPathNoSubdir(self):
    self.assertEqual(
      "Szła dzieweczka do laseczka",
      m.TitleFromPath("content/Szła_dzieweczka_do_laseczka.md"))

  def testTitleFromPathWithSlash(self):
    self.assertEqual("F7/C", m.TitleFromPath(Path("content/książka/F7/C.md")))

  def testTitleFromPathWithSlashNoSubdir(self):
    self.assertEqual("F7/C", m.TitleFromPath(Path("content/F7/C.md")))

  def testIsNoteName(self):
    self.assertTrue(m._isNoteName('C'))
    self.assertTrue(m._isNoteName('C♯'))
    self.assertTrue(m._isNoteName('D♭'))
    self.assertFalse(m._isNoteName('T'))

  def testSlugify(self):
    self.assertEqual(
      "szła-dzieweczka-do-laseczka",
      m.Slugify("Szła dzieweczka do laseczka"))

  def testSlugifyUnder(self):
    self.assertEqual("foo-bar", m.Slugify("foo_bar"))

  def testSlugifyEndings(self):
    self.assertEqual("foo-bar", m.Slugify("(foo_bar)"))

  def testRenderFrontMatter(self):
    fm = m.FrontMatter(title="Test title 1", slug="test-title-1")
    fm.wikilinks.append(m.Wikilink("Another article", "Another_article"))
    fm.categories.append("B-test-category")
    fm.categories.append("A-test-category")
    fm.aliases.append("B-alias")
    fm.aliases.append("A-alias")
    fm.contributor = "Zenek"
    expected = """---
title: "Test title 1"
slug: "test-title-1"
date: 2005-01-01T00:00:00+01:00
kategorie: ['A-test-category', 'B-test-category']
draft: false
contributor: 'Zenek'
wikilinks: ['Another_article']
aliases: ['A-alias', 'B-alias']
---
"""
    self.assertEqual(expected, fm.ToString())

  def testRenderFrontMatterNoAliases(self):
    fm = m.FrontMatter(title="Test title 1", slug="test-title-1")
    fm.wikilinks.append(m.Wikilink("Another article", "Another_article"))
    fm.categories.append("test-category")
    expected = """---
title: "Test title 1"
slug: "test-title-1"
date: 2005-01-01T00:00:00+01:00
kategorie: ['test-category']
draft: false
wikilinks: ['Another_article']
---
"""
    self.assertEqual(expected, fm.ToString())

  def testRenderFrontMatterImages(self):
    fm = m.FrontMatter(title="Test title 1", slug="test-title-1")
    fm.wikilinks.append(m.Wikilink("Another article", "Another_article"))
    fm.categories.append("test-category")
    fm.image_paths = ["img1", "img2"]
    expected = """---
title: "Test title 1"
slug: "test-title-1"
date: 2005-01-01T00:00:00+01:00
kategorie: ['test-category']
draft: false
wikilinks: ['Another_article']
images:
  - path: "img1"
  - path: "img2"
---
"""
    self.assertEqual(expected, fm.ToString())

  def testWikilinks(self):
    redirects = {}
    dest_doc = m.Document(
      "dest doc", Path("książka/Modulatory_i_filtry_dźwięku.md"), None)
    doc = m.Document('3.  [Modulatory i filtry dźwięku]'
                     '(Modulatory_i_filtry_dźwięku "wikilink")',
                     Path('książka/foo.md'), None)
    docs = (dest_doc, doc)
    by_path = m.DocumentsByPath(docs)
    by_wikiname = m.DocumentsByWikiname(docs)
    dst = ('3.  [Modulatory i filtry dźwięku]'
          '({{< relref "Modulatory_i_filtry_dźwięku.md" >}})')
    self.assertEqual(dst, doc.TryToFixWikilinks(by_path, by_wikiname, redirects).content)

  def testWikilinksParen(self):
    redirects = {}
    dest_doc = m.Document(
      "", Path("content/książka/Bossa_Nova_(akompaniament).md"), None)
    doc = m.Document('[Coś tam (akompaniament)](Bossa_Nova_(akompaniament) '
                     '"wikilink")', Path('content/książka/foo.md'), None)
    docs = (dest_doc, doc)
    by_path = m.DocumentsByPath(docs)
    by_wikiname = m.DocumentsByWikiname(docs)
    dst = ('[Coś tam (akompaniament)]'
           '({{< relref "Bossa_Nova_(akompaniament).md" >}})')
    self.assertEqual(dst, doc.TryToFixWikilinks(by_path, by_wikiname, redirects).content)

  def testWikilinksCategory(self):
    m.CATEGORY_TAG = "kategoria"
    redirects = {}
    dest_doc = m.Document(
      "", Path("content/książka/Bossa_Nova_\\(akompaniament\\).md"), None)
    doc = m.Document('[some anchor](:Kategoria:Tabele_chwytów "wikilink") a',
                     Path('content/książka/foo.md'), None)
    by_path = {m.path: m for m in (dest_doc, doc)}
    by_wikiname = m.DocumentsByWikiname((dest_doc, doc))
    dst = ('[some anchor]'
           '(/kategorie/tabele-chwytow "Kategoria Tabele chwytów")'
           ' a')
    self.assertEqual(dst, doc.TryToFixWikilinks(by_path, by_wikiname, redirects).content)

  def testLowercaseWikilink(self):
    m.CATEGORY_TAG = "kategoria"
    redirects = {}
    doc_akord = m.Document('O akordzie',
                     Path('content/książka/Akord.md'), None)
    doc = m.Document('[akord](akord "wikilink")',
                     Path('content/książka/foo.md'), None)
    by_path = m.DocumentsByPath((doc_akord, doc))
    by_wikiname = m.DocumentsByWikiname((doc_akord, doc))
    dst = ('[akord]({{< relref "Akord.md" >}})')
    self.assertEqual(dst, doc.TryToFixWikilinks(by_path, by_wikiname, redirects).content)

  def testSingleRedirect(self):
    m.CATEGORY_TAG = "kategoria"
    redirects = {m.Wikiname('Akord'): m.Wikiname('Bakord')}
    doc_akord = m.Document('', Path('książka/Akord.md'), None)
    doc_bakord = m.Document('O akordzie', Path('książka/Bakord.md'), None)
    doc = m.Document('[akord](akord "wikilink")', Path('książka/foo.md'), None)
    docs = (doc_akord, doc_bakord, doc)
    by_path = m.DocumentsByPath(docs)
    by_wikiname = m.DocumentsByWikiname(docs)
    dst = ('[akord]({{< relref "Bakord.md" >}})')
    self.assertEqual(dst, doc.TryToFixWikilinks(by_path, by_wikiname, redirects).content)

  def testDoubleRedirect(self):
    m.CATEGORY_TAG = "kategoria"
    redirect_untyped = (('Akord', 'Bakord'), ('Bakord', 'Cakord'))
    redirects = {m.Wikiname(a): m.Wikiname(b) for a, b in redirect_untyped}
    doc_akord_0 = m.Document('', Path('content/książka/Akord.md'), None)
    doc_akord_1 = m.Document('', Path('content/książka/Bakord.md'), None)
    doc_akord_2 = m.Document('O akordzie', Path('content/książka/Cakord.md'), None)
    doc = m.Document('[akord](akord "wikilink")', Path('content/książka/foo.md'), None)
    docs = (doc_akord_0, doc_akord_1, doc_akord_2, doc)
    by_path = m.DocumentsByPath(docs)
    by_wikiname = m.DocumentsByWikiname(docs)
    dst = ('[akord]({{< relref "Cakord.md" >}})')
    self.assertEqual(dst, doc.TryToFixWikilinks(by_path, by_wikiname, redirects).content)

  def testWikilinksCategoryWithSpaces(self):
    m.CATEGORY_TAG = "kategoria"
    doc = m.Document('[kategoria:technika gry](kategoria:technika_gry "wikilink")',
                     'foo/Page_Title.md', None)
    self.assertIn('Technika gry', doc.fm.categories)

  def testCategoryWithDiacritics(self):
    m.CATEGORY_TAG = "kategoria"
    doc = m.Document('[Pass, Joe](kategoria:gitarzyści_jazzowi "wikilink")',
                     'content/książka/foo.md', None)
    by_path = {m.path: m for m in (doc,)}
    self.assertIn("Gitarzyści jazzowi", doc.fm.categories)

  def testRemoveCategoryLinks(self):
    m.CATEGORY_TAG = 'kategoria'
    doc = m.Document(
      'head[kategoria:technika gry](# "Niestety nic nie ma pod '
      'tym linkiem")tail', 'content/książka/foo.md', None)
    dst = 'headtail'
    self.assertEqual(dst, doc.RemoveCategoryLinks().content)

  def testRedirection(self):
    doc = m.Document(
      '1.  REDIRECT [Regulacja gryfu](Regulacja_gryfu "wikilink")',
      Path('foo/bar.md'), None)
    self.assertEqual("Regulacja_Gryfu" , doc.GetRedirect())

  def testRedirectionStruna(self):
    doc = m.Document(
      '1.  REDIRECT [Struna](Struna "wikilink")',
      Path('foo/bar.md'), None)
    self.assertEqual("Struna", doc.GetRedirect())

  def testURLPathTopLevel(self):
    doc = m.Document(
      '1.  REDIRECT [C9sus](C9sus "wikilink")',
      Path('foo.md'), None)
    self.assertEqual("C9Sus", doc.GetRedirect())
    self.assertEqual("foo", doc.fm.slug)
    self.assertEqual("/foo", doc.URLPath())

  def testURLPathWithSlash(self):
    doc = m.Document(
      '1.  REDIRECT [C9sus](C9sus "wikilink")',
      Path('książka/B♭/C.md'), None)
    self.assertEqual("C9Sus", doc.GetRedirect())
    self.assertEqual("b-c", doc.fm.slug)
    self.assertEqual("/książka/b-c", doc.URLPath())

  def testHandleImageTags(self):
    m.IMAGE_TAG = 'grafika'
    doc = m.Document(
      '[thumb](Grafika:MarekBlizinskiPozycja.jpg "wikilink") - postawa z',
      Path('foo/bar.md'), None)
    self.assertEqual(
      '{{< image src="/images/MarekBlizinskiPozycja.jpg" >}} - postawa z',
      doc.HandleImageTags().content)

  def testHandleImageTagsLowercase(self):
    m.IMAGE_TAG = 'grafika'
    doc = m.Document(
      '[thumb](Grafika:plectrum1.jpg "wikilink") - postawa z',
      Path('foo/bar.md'), None)
    self.assertEqual('{{< image src="/images/Plectrum1.jpg" >}} - postawa z',
                     doc.HandleImageTags().content)

  def testHandleImageTagsMultiline(self):
    m.IMAGE_TAG = 'grafika'
    doc = m.Document(
      '[thumb\nnail](Grafika:MarekBlizinskiPozycja.jpg "wikilink") - postawa z',
      'foo/bar.md', None)
    self.assertEqual(["/images/MarekBlizinskiPozycja.jpg"], doc.fm.image_paths)
    self.assertEqual(
      '{{< image src="/images/MarekBlizinskiPozycja.jpg" >}} - postawa z',
      doc.HandleImageTags().content)

  def testHandleImageTagsMultipleImages(self):
    m.IMAGE_TAG = 'grafika'
    doc = m.Document(
      '[thumb\nnail](Grafika:MarekBlizinskiPozycja.jpg "wikilink") - postawa z'
      '[somethingelse](Grafika:anotherImage.jpg "wikilink")',
      'foo/bar.md', None)
    self.assertEqual(["/images/MarekBlizinskiPozycja.jpg",
                      "/images/AnotherImage.jpg"], doc.fm.image_paths)

  def testBackwardCompatibilityURL(self):
    doc = m.Document('test content', 'foo/Page_Title.md', None)
    self.assertIn('/gitara/Page_Title', doc.fm.aliases)

  def testFixMonospace(self):
    doc = m.Document('a\n`line1`\n`line2`\nb\n', 'foo/Page_Title.md', None)
    self.assertEqual('a\n\n```\nline1\nline2\n```\n\nb\n', doc.FixMonospace().content)


if __name__ == '__main__':
  logging.basicConfig(level=logging.INFO)
  unittest.main()
