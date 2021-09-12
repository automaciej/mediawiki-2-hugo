#!/usr/bin/python
"""Add Hugo front matter to Mediawiki markdown pages.

Used on output from:
https://github.com/outofcontrol/mediawiki-to-gfm
"""

from typing import Dict, Iterable, List, NewType, Optional, Set, Tuple
from dataclasses import dataclass, field

import argparse
import commonmark  # type: ignore
import difflib
import itertools
import logging
import os
import os.path
import re
import shutil
import toml
import pathlib
import unidecode
import urllib.parse
import xml.etree.ElementTree as ET


Wikiname = NewType('Wikiname', str)


# Language dependent
CATEGORY_TAG = "Category"
IMAGE_TAG = "Graphics"


@dataclass
class Link:
  anchor: str
  url: str
  title: str


@dataclass
class Wikilink:
  anchor: str
  destination: str


@dataclass
class FrontMatter:
  title: str
  slug: str
  date: str = field(init=False, default="2005-01-01T00:00:00+01:00")
  categories: List[str] = field(init=False, default_factory=list)
  links: List[Link] = field(init=False, default_factory=list)
  wikilinks: List[Wikilink] = field(init=False, default_factory=list)
  redirect: Optional[Wikiname] = field(init=False, default=None)
  aliases: List[str] = field(init=False, default_factory=list)
  image_paths: List[str] = field(init=False, default_factory=list)
  # Metadata from Mediawiki XML export.
  timestamp: Optional[str] = field(init=False, default="")
  contributor: Optional[str] = field(init=False, default="")
  wiki_name: Optional[Wikiname] = field(init=False, default=None)
  slug_with_diacritics: Optional[str] = field(init=False, default=None)

  def ToString(self) -> str:
    wiki_destinations = [f"{wl.destination}" for wl in self.wikilinks]
    wikilinks_text = f"wikilinks: {sorted(wiki_destinations)}"
    if self.aliases:
      aliases_text = f"aliases: {sorted(self.aliases)}\n"
    else:
      aliases_text = ""
    if self.image_paths:
      image_text = "images:\n" + "\n".join([f"  - path: \"{x}\"" for x in
                                            sorted(self.image_paths)]) + "\n"
    else:
      image_text = ""
    if self.contributor:
      contributor = f"contributor: {self.contributor!r}\n"
    else:
      contributor = ""
    return f"""---
title: "{self.title}"
slug: "{self.slug}"
date: {self.date}
kategorie: {sorted(self.categories)}
draft: false
{contributor}{wikilinks_text}
{aliases_text}{image_text}---
"""


@dataclass
class MediawikiPage:
  title: str
  timestamp: str
  contributor: str


@dataclass
class Document:
  """Represents a Markdown document."""
  content: str
  path: pathlib.Path
  mp: Optional[MediawikiPage]
  fm: FrontMatter = field(init=False, default_factory=lambda: FrontMatter("", ""))

  def __post_init__(self):
    # It would be even better if we could initialize FrontMatter from the
    # default factory. All the information we need is in content and path. But
    # I don't think we can pass content and path to default_factory.
    # Instead we'll replace the empty FrontMatter with one with data.
    self.fm = self.MakeFrontMatter()

  def URLPath(self):
    """The URL path to access this document from, for redirects.

    This is not generic enough. In this case it's hard to predict what the URL
    will be, because it depends on the target Hugo configuration. In theory this
    could take the Hugo config and work off of that, but... it's too complex for
    me to implement to fix like 5 URLs.
    """
    segments = self.path.parts
    if len(segments) > 1:
      segments = segments[:1]  # only 1 URL depth
    else:
      segments = []
    return "/" + "/".join(itertools.chain(segments, [self.fm.slug]))

  def GetRedirect(self) -> Optional[Wikiname]:
    """If the document is a redirection, return the destination wiki name."""
    anchor_pat = '\[(?P<anchor>[^\]]+)\]'
    redir_pat = 'REDIRECT\\s+' + anchor_pat + '\((?P<dest>[^\s]+) "wikilink"\)'
    m = re.search(redir_pat, self.content)
    return Wikiname(m['dest'].title()) if m else None

  def MakeFrontMatter(self) -> FrontMatter:
    title = TitleFromPath(self.path)
    parser = commonmark.Parser()
    ast = parser.parse(self.content)
    if ast is None:
      raise Exception("parsing of {self.path!r} markdown failed")
    # Diacritics in URL paths encode ugly, for example "książka" becomes a
    # "ksi%C4%85%C5%BCka". Words with diacritics removed ("ksiazka") are also
    # ugly, but they are more readable.
    # There might be a mitigation by using HTTP temporary redirects.
    # Discussed here:
    # https://serverfault.com/questions/1076344/temporary-redirects-302-307-on-a-static-site-frequently-updated
    bald_slug = Slugify(unidecode.unidecode(title))
    fm = FrontMatter(title=title, slug=bald_slug)
    fm.wiki_name = Wikiname(fm.title.replace(" ", "_").title())
    fm.slug_with_diacritics = Slugify(title)
    # ast.walker seems to visit some nodes more than once.
    # This is surprising.
    already_seen = set()
    for node, unused_entering in ast.walker():
      if node in already_seen:
        continue
      already_seen.add(node)
      if node.t == "link":
        anchor = node.first_child.literal
        url = node.destination
        title = node.title
        category_pat = f"{CATEGORY_TAG}:(?P<category>.*)"
        # Links targets starting with a ":" mean that the page in question does
        # not itself belong to the category, but only links to it.
        m = re.match(category_pat, url, flags=re.IGNORECASE)
        if m:
          category = (urllib.parse.unquote_plus(m['category'])
                      .replace('_', ' ').capitalize())
          fm.categories.append(category)
        elif title == "wikilink":
          fm.wikilinks.append(Wikilink(anchor, url))
        else:
          fm.links.append(Link(anchor, url, title))
    fm.redirect = self.GetRedirect()
    # Identify images on the page.
    # TODO: Dedup image pattern.
    image_pattern = '\[[^\]]+\]\(' + IMAGE_TAG + ':([^\s]+)\s"wikilink"\)'
    for m in re.finditer(image_pattern, self.content, flags=re.IGNORECASE):
      # Use first found image as the entry image.
      # TODO: Deduplicate the image path.
      image_path = "/images/" + m.group(1)[0].upper() + m.group(1)[1:]
      fm.image_paths.append(image_path)

    # Metadata from Mediawiki
    if self.mp:
      fm.date = self.mp.timestamp
      fm.contributor = self.mp.contributor

    # This doesn't work because aliases also need the path. The slug is not the
    # full URL path.
    # if fm.slug_with_diacritics != fm.slug:
    #   fm.aliases.append(fm.slug_with_diacritics)
    return fm

  def TryToFixWikilinks(self,
                        by_path: Dict[pathlib.Path, 'Document'],
                        by_wikiname: Dict[Wikiname, 'Document'],
                        redirects: Dict[Wikiname, Wikiname]) -> 'Document':
    """When the target file does not exist on disk, don't sub."""
    # Pattern matching the destination.
    dest_pattern = '[^\s]+'
    anchor_pat = '\[(?P<anchor>[^\]]+)\]'
    identify_pat = anchor_pat + '\((?P<dest>[^\s]+) "wikilink"\)'
    def repl(m: Optional[re.Match[str]]) -> str:
      if m is None:
        # re.sub seems to declare that this function is called with
        # Optional[Match], but I think in practice this function only gets
        # called when there's a match. So the below line should never execute.
        raise ValueError("called with a None, this should not happen")
      dest = m['dest']
      dest_wikiname = Wikiname(m['dest'].title())
      anchor: str = m['anchor']
      def annotate_invalid(s: str, reason: str) -> str:
        logging.info("Unable to fix link [%r](%r) in %r: %s", anchor, dest,
                     self.path, reason)
        return f"{s}<!-- link nie odnosił się do niczego: {reason} -->"
      def RedirectExists(wikiname: Wikiname) -> bool:
        return wikiname in redirects and redirects[wikiname] in by_wikiname
      def ResolvePathRedirect(p: pathlib.Path) -> Optional[Document]:
        logging.debug("ResolveRedirect(%r)", p)
        return ResolveRedirect(WikinameFromPath(p))
      def ResolveRedirect(wikiname: Wikiname) -> Optional[Document]:
        doc: Optional[Document] = None
        visited: Set[Wikiname] = set()
        wikiname_title = Wikiname(wikiname.title())
        while RedirectExists(wikiname_title):
          assert wikiname_title not in visited, (
            f"Redirect loop for {wikiname}, visited: {visited}")
          visited.add(wikiname_title)
          logging.debug("Found a redirect from %r to %r",
                        wikiname_title, redirects[wikiname_title])
          wikiname_title = Wikiname(redirects[wikiname_title].title())
          doc = by_wikiname[wikiname_title]
          assert wikiname_title == doc.fm.wiki_name, (
            f"{wikiname_title!r} != {doc.fm.wiki_name!r}")
        if doc is not None and doc.fm.redirect is not None:
          raise ValueError(f"Trying to redirect to a redirection")
        return doc
      # Resolve redirections.
      target_doc: Optional[Document] = ResolveRedirect(dest_wikiname)
      # Categories don't have a path.
      dest_path = pathlib.Path('/no/path/exists')
      if target_doc is not None:
        dest_path = target_doc.path
      elif dest_wikiname in by_wikiname:
        target_doc = by_wikiname[dest_wikiname]
        dest_path = target_doc.path
      dest_ref: str
      if target_doc is not None:
        assert target_doc.path in by_path
        dest_ref = target_doc.fm.title.replace(' ', '_') + '.md'
      elif dest_path in by_path:
        target_doc = by_path[dest_path]
        dest_ref = target_doc.path.parts[-1]
      elif re.match(':'+CATEGORY_TAG+':', dest, flags=re.IGNORECASE):
        m = re.search(':'+CATEGORY_TAG+':(?P<category>.*):?', dest, re.IGNORECASE)
        if m is None:
          return annotate_invalid(anchor, "Could not find the category tag")
        category = m['category']
        # TODO: Customize the category URL path from "kategorie"
        slug = Slugify(unidecode.unidecode(category))
        return "[%s](/kategorie/%s \"Kategoria %s\")" % (
          anchor, slug, category.replace("_", " "))
      else:
        msg = ("%r (%r) links to %r (%r) and that does not exist" % (
          self.fm.title, self.path, dest, dest_path))
        return annotate_invalid(anchor, msg)
      return '[%s]({{< relref "%s" >}})' % (anchor, dest_ref)
    return Document(re.sub(identify_pat, repl, self.content),
                    self.path, self.mp)

  def RemoveCategoryLinks(self) -> 'Document':
    pattern = '\[:?' + CATEGORY_TAG + ':[^\]]+\]\([^\)]+\)'
    return Document(re.sub(pattern, '', self.content, flags=re.IGNORECASE),
                    self.path, self.mp)

  def HandleImageTags(self) -> 'Document':
    # TODO: Dedup image pattern.
    image_pattern = '\[[^\]]+\]\(' + IMAGE_TAG + ':([^\s]+)\s"wikilink"\)'
    def repl(m):
      # Image path is always capitalized in MediaWiki, and works even if you
      # don't capitalize it in page text.
      image_path = "/images/" + m.group(1)[0].upper() + m.group(1)[1:]
      return '{{< image src="' + image_path + '" >}}'

    return Document(re.sub(image_pattern, repl, self.content, flags=re.IGNORECASE),
                    self.path, self.mp)

  def FixMonospace(self) -> 'Document':
    _outside = 0
    _inside = 1
    result = []
    state = _outside
    pattern = re.compile('^`(?P<monospace>.*)`$')
    for line in self.content.splitlines(keepends=False):
      if state == _outside:
        m = re.match(pattern, line)
        if m is not None:
          result.extend(['', '```', m['monospace']])
          state = _inside
        else:
          result.append(line)
      elif state == _inside:
        m = re.match(pattern, line)
        if m is not None:
          result += [m['monospace']]
        else:
          result.extend(['```', '', line])
          state = _outside
    return Document('\n'.join(result) + '\n', self.path, self.mp)


def Slugify(s: str) -> str:
  no_under = s.replace('_', ' ')
  lowercased = no_under.lower()
  segments = re.split("[^\w]+", lowercased)
  return ("-".join(segments)).strip('-')


def DocumentFromPath(root_dir: pathlib.Path, path: pathlib.Path, existing_paths:
                     Set[pathlib.Path],
                     data_from_xml: Dict[str, MediawikiPage]) -> Optional[Document]:
  # First things first, let's check if we're even going to try.
  with open(os.path.join(root_dir, path), "rb") as fd:
    content_bytes = fd.read()
  markdown_text = content_bytes.decode("utf-8")
  for fm_delimiter in ('---', '+++', '{'):
    if markdown_text.startswith(fm_delimiter):
      logging.info(
        "%r seems to contain Front Matter already, %r found; skipping",
        path, fm_delimiter)
      return None
  title = TitleFromPath(path)
  mp: Optional[MediawikiPage] = None
  if title in data_from_xml:
    mp = data_from_xml[title]
  else:
    mp = None
  return Document(markdown_text, path, mp)


def WriteContent(content: str, path: str, dry_run: bool) -> bool:
  content_bytes = content.encode('utf-8')
  # Is there a diff?
  try:
    with open(path, "rb") as fd:
      existing_content = fd.read()
      if content_bytes == existing_content:
        logging.debug('No diffs found for %r', path)
        return False
  except FileNotFoundError:
    # That's fine.
    pass

  if not dry_run:
    with open(path, "wb") as fd:
      fd.write(content_bytes)
  else:
    logging.info("[dry run] Would write to %r", path)
  return True


def MarkdownPaths(dirname: str) -> Set[pathlib.Path]:
  """Return the list of files relative to the source directory."""
  file_list = set()
  for root, dirs, files in os.walk(dirname):
    for f in files:
      fullpath = pathlib.Path(os.path.join(root, f))
      relpath = fullpath.relative_to(dirname)
      if f.endswith('.md'):
        file_list.add(relpath)
  return file_list


def DocumentsByPath(documents: Iterable[Document]) -> Dict[pathlib.Path, Document]:
  by_path: Dict[pathlib.Path, Document] = {}
  for doc in documents:
    assert doc.path not in by_path
    by_path[doc.path] = doc
    # For compatibility with Mediawiki, we're considering paths
    # case-insensitive.
    path_lower = pathlib.Path(str(doc.path).lower())
    if path_lower not in by_path:
      by_path[path_lower] = doc
  return by_path


def DocumentsByWikiname(documents: Iterable[Document]) -> Dict[Wikiname, Document]:
  by_wikiname: Dict[Wikiname, Document] = {}
  for d in documents:
    if d.fm.wiki_name is None:
      continue
    by_wikiname[Wikiname(d.fm.wiki_name.title())] = d
  return by_wikiname


def _isNoteName(s: str) -> bool:
  flat = '♭'
  sharp = '♯'
  letters = [chr(x) for x in range(ord('A'), ord('H'))]
  with_flat = [x + flat for x in letters]
  with_sharp = [x + sharp for x in letters]
  notes = set(itertools.chain(letters, with_flat, with_sharp))
  return s in notes


def WikinameFromPath(path: pathlib.Path) -> Wikiname:
  """Derive the Wikiname from path.

  content/książka/Foo.md => Foo
  content/książka/Foo_Bar.md => 'Foo_Bar'

  Also support chord names with slashes:

  content/książka/F/C.md => F/C
  content/F/C.md => F/C
  """
  no_ext, _ = os.path.splitext(path)
  parts = no_ext.split("/")
  # Special case for chords.
  collapse_last_slash = _isNoteName(parts[-1])
  if collapse_last_slash and len(parts) >= 2:
    use_for_title = parts[-2] + '/' + parts[-1]
  else:
    use_for_title = parts[-1]
  return Wikiname(use_for_title)

def TitleFromPath(path: pathlib.Path) -> str:
  """Similar from Wikiname, but with spaces."""
  return str(WikinameFromPath(path)).replace("_", " ")


def ValidateDirectories(dir1: str, dir2: str) -> bool:
  """Return true when both directories exist and are different.

  Following the footsteps of giants, here's a quote from `rm` from Illumos:
    https://github.com/illumos/illumos-gate/blob/9ecd05bdc59e4a1091c51ce68cce2028d5ba6fd1/usr/src/cmd/rm/rm.c#L446-L448
  """
  try:
    st1 = os.stat(dir1)
    st2 = os.stat(dir2)
  except FileNotFoundError as e:
    raise FileNotFoundError(
      f"Please sure both directories exist: {dir1!r}, {dir2!r}") from e
  return st1.st_dev == st2.st_dev and st1.st_ino == st2.st_ino


if __name__ == '__main__':
  parser = argparse.ArgumentParser(
      description="Convert markdown from mediawiki-to-gfm to hugo.")
  parser.add_argument(
    "source_directory", metavar="SRC_PATH",
    help="Source directory, same layout as 'content'.")
  parser.add_argument(
    "content_directory", metavar="DST_PATH",
    help="Content directory, usually named 'content'.")
  parser.add_argument(
    "--category-tag", metavar="TAG", default="Category",
    help="Name of the Category tag in Mediawiki. This tends to be "
         "language-dependent. Non-English Mediawiki instances will use "
         "different words, like Catégorie or Kategoria.")
  parser.add_argument(
    "--image-tag", metavar="TAG", default="File",
    help="Name of the Image tag in Mediawiki.")
  parser.add_argument(
    "--xml-data", metavar="XML_PATH", default=None,
    help="Path to the XML export from Mediawiki")
  parser.add_argument('--dry_run', default=False, action='store_true',
                      help="Don't make changes on the filesystem")
  args = parser.parse_args()
  ValidateDirectories(args.source_directory, args.content_directory)
  CATEGORY_TAG = args.category_tag
  IMAGE_TAG = args.image_tag
  logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)-8s %(filename)s:%(lineno)d - %(message)s')
  # TODO: Make this script work from other locations tool.
  assert args.content_directory == 'content', (
    "You need to be in Hugo root and use the argument 'content', e.g. "
    "python3 utils/mediawiki_markdown_to_hugo.py content"
  )

  # To avoid funky path manipulation, it would be easy to os.chdir into the
  # source directory, read, then os.chdir into the target directory and write.
  # But I'm under the impression that going back to the original directory is
  # not trivial, because I'd have to translate the path to the absolute path,
  # and it feels messy. So I guess I'll have to do it differently, by tracking
  # file paths starting from the source directory.
  markdown_paths = MarkdownPaths(args.source_directory)
  if not markdown_paths:
    raise Exception(
      f"Could not find any *.md files in {args.source_directory!r}")
  logging.info("Found %d markdown paths, for example %s", len(markdown_paths),
               next(iter(markdown_paths)))

  data_from_xml: Dict[str, MediawikiPage] = {}
  # Why can't I get it from the XML itself?
  ns = {'mw': 'http://www.mediawiki.org/xml/export-0.10/'}
  # Read the XML export if it exists.
  def Value(page, name) -> str:
    maybe_element = page.find(name, ns)
    if maybe_element is not None:
      return '\n'.join(maybe_element.itertext())
    raise ValueError(f"Incomplete data in XML {page!r} for {name!r}")

  if args.xml_data:
    tree = ET.parse(args.xml_data)
    root = tree.getroot()
    for page in root.findall('mw:page', ns):
      try:
        title = Value(page, 'mw:title')
        timestamp = Value(page, 'mw:revision/mw:timestamp')
        contributor = Value(page, './/mw:contributor/mw:username')
        data_from_xml[title] = MediawikiPage(title, timestamp, contributor)
      except ValueError:
        pass

  documents: Dict[Wikiname, Document] = {}
  for path in markdown_paths:
    doc = DocumentFromPath(args.source_directory, path, markdown_paths, data_from_xml)
    if doc is None:
      continue
    wiki_name = doc.fm.wiki_name
    assert wiki_name not in documents, (
      f"Page {wiki_name!r} ({doc.path}) is already in documents: "
      f"{documents[wiki_name].path}")
    if wiki_name is not None:
      documents[wiki_name] = doc

  by_path: Dict[pathlib.Path, Document] = DocumentsByPath(documents.values())
  by_wikiname: Dict[Wikiname, Document] = DocumentsByWikiname(documents.values())

  redirects: Dict[Wikiname, Wikiname] = {}
  # Need to find the redirects, and assign aliases.
  for wiki_name, doc in documents.items():
    if doc.fm.redirect is None:
      continue
    if doc.fm.redirect in by_wikiname:
      target_doc = by_wikiname[doc.fm.redirect]
      target_doc.fm.aliases.append(doc.URLPath())
      if doc.fm.wiki_name is not None:
        redirects[Wikiname(doc.fm.wiki_name.title())] = doc.fm.redirect
      else:
        ValueError(r"{doc.path!r} is a redirect but has no wiki name")
    elif re.match(':'+CATEGORY_TAG+':', doc.fm.redirect,
                     flags=re.IGNORECASE):
      # TODO: A redirection to a category page.
      logging.warning("%r tries to redirect to a category page: %r", doc.path, doc.fm.redirect)
    else:
      logging.warning(f"Bad redirect: {doc.fm.redirect!r}")


  writing_result: Dict[pathlib.Path, bool] = {}
  number_files_written = 0
  for doc in documents.values():
    if doc.fm.redirect is not None:
      logging.info("Not writing %r because it's a redirect to %r", doc.path,
                   doc.fm.redirect)
      continue
    updated_content: str = doc.fm.ToString() + (doc.RemoveCategoryLinks()
                          .HandleImageTags()
                          .TryToFixWikilinks(by_path, by_wikiname, redirects)
                          .FixMonospace()
                          .content)

    written = WriteContent(updated_content,
                           os.path.join(args.content_directory, doc.path),
                           args.dry_run)
    writing_result[doc.path] = written
    number_files_written += 1
  dry_run_section = "(would have) " if args.dry_run else ""
  print(f"{dry_run_section} written {number_files_written} files.")
