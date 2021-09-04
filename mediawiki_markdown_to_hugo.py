#!/usr/bin/python
"""Add Hugo front matter to Mediawiki markdown pages.

Used on output from:
https://github.com/outofcontrol/mediawiki-to-gfm

Known issues:

- Need to clean up the wikilinks; serialize AST back to Markdown.
- Read missing data from the Mediawiki XML export.

Q: The script messed up my directory! I want to restore the previous files!
A: for f in *.orig; do mv -v "${f}" "${f/.orig}"; done

TODO: Make the above recursive? This is surprisingly nontrivial.

This doesn't work: 

```
find content -name '*.md.orig' -exec \
    mv -f {} "$(echo -n {} | sed -e 's/\\.orig$//')" \;
```

The reason is quite funny: What `find` gets in argv is `mv -f {} {}` because the
"$( ... )" block gets executed by shell before `find` has a chance to see it.
"""

from typing import Dict, Iterable, List, Optional, Set, Tuple
from dataclasses import dataclass, field

import argparse
import commonmark  # type: ignore
import itertools
import logging
import os
import os.path
import re
import shutil
import toml
import unidecode
import urllib.parse
import xml.etree.ElementTree as ET


# Language dependent
CATEGORY_TAG = "Category"
IMAGE_TAG = "Graphics"
BACKUP_EXT = ".orig"


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
  redirect: Optional[str] = field(init=False, default=None)
  aliases: List[str] = field(init=False, default_factory=list)
  image_paths: List[str] = field(init=False, default_factory=list)
  # Metadata from Mediawiki XML export.
  timestamp: Optional[str] = field(init=False, default="")
  contributor: Optional[str] = field(init=False, default="")
  wiki_name: Optional[str] = field(init=False, default=None)

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
  path: str
  mp: Optional[MediawikiPage]
  fm: FrontMatter = field(init=False, default_factory=lambda: FrontMatter("", ""))

  def __post_init__(self):
    # It would be even better if we could initialize FrontMatter from the
    # default factory. All the information we need is in content and path. But
    # I don't think we can pass content and path to default_factory.
    # Instead we'll replace the empty FrontMatter with one with data.
    self.fm = self.MakeFrontMatter()

  def TryToFixWikilinks(self,
                        by_path: Dict[str, 'Document'],
                        redirects: Dict[str, str]) -> 'Document':
    """When the target file does not exist on disk, don't sub."""
    # Pattern matching the destination.
    dest_pattern = '[^\s]+'
    anchor_pat = '\[(?P<anchor>[^\]]+)\]'
    identify_pat = anchor_pat + '\((?P<dest>[^\s]+) "wikilink"\)'
    def repl(m) -> str:
      dest = m['dest']
      anchor = m['anchor']
      # We've found a destination, does it exist on disk?
      # Desperate measures here. I wanted this to not do I/O.
      # This is also not configured correctly and won't work on anyone else's
      # setup.
      doc_dir, _ = os.path.split(self.path)
      dest_path = os.path.join(doc_dir, dest + ".md")
      def annotate_invalid(s: str, reason: str) -> str:
        logging.info("Could not fix link [%r](%r) in %r: %s", anchor, dest,
                     self.path, reason)
        return f"{s}<!-- link nie odnosił się do niczego: {reason} -->"
      def ResolveRedirect(p: str) -> Optional[Document]:
        doc = None
        while p in redirects and redirects[p] in by_path:
          doc = by_path[redirects[p]]
          p = doc.path
        return doc
      target_doc = ResolveRedirect(dest_path)
      if target_doc is not None:
        dest_name = target_doc.fm.title.replace(' ', '_')
      elif dest_path in by_path and by_path[dest_path].GetRedirect():
        target_doc = by_path[dest_path]
        after_redirection = ResolveRedirect(target_doc.path)
        if after_redirection is None:
          msg = ("%r wants to redirect to %r, but %r will be deleted" % (
                 dest_path, target_doc.path, target_doc.path))
          return annotate_invalid(anchor, msg)
        else:
          target_doc = after_redirection
          dest_name = target_doc.fm.title.replace(' ', '_') + '.md'
      elif dest_path in by_path:
        target_doc = by_path[dest_path]
        dest_name = target_doc.fm.title.replace(' ', '_') + '.md'
      elif re.match(':'+CATEGORY_TAG+':', dest, flags=re.IGNORECASE):
        m = re.search(':'+CATEGORY_TAG+':(?P<category>.*):?', dest, re.IGNORECASE)
        if m is None:
          return annotate_invalid(anchor, "Could not find the category tag")
        category = m['category']
        # TODO: Customize the category URL path from "kategorie"
        slug = Slugify(category)
        return "[%s](/kategorie/%s \"Kategoria %s\")" % (
          anchor, slug, category.replace("_", " "))
      else:
        msg = ("%r (%r) links to %r (%r) and that does not exist" % (
          self.fm.title, self.path, dest, dest_path))
        return annotate_invalid(anchor, msg)
      return '[%s]({{< relref "%s" >}})' % (anchor, dest_name)
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
      return '{{< figure src="' + image_path + '" >}}'

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

  def GetRedirect(self) -> Optional[str]:
    """If the document is a redirection, return the destination."""
    anchor_pat = '\[(?P<anchor>[^\]]+)\]'
    redir_pat = 'REDIRECT\\s+' + anchor_pat + '\((?P<dest>[^\s]+) "wikilink"\)'
    m = re.search(redir_pat, self.content)
    return m['dest'] if m else None

  def MakeFrontMatter(self) -> FrontMatter:
    title = TitleFromPath(self.path)
    parser = commonmark.Parser()
    ast = parser.parse(self.content)
    if ast is None:
      raise Exception("Parsing failed?")
    fm = FrontMatter(title=title, slug=Slugify(title))
    fm.wiki_name = fm.title.replace(" ", "_")
    bald_slug = NoDiacriticsSlugify(title)
    if bald_slug != fm.slug:
      fm.aliases.append(bald_slug)
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

    return fm

  def URLPath(self):
    """The URL path to access this document from, for redirects."""
    segments = self.path.split("/")
    segments = segments[1:]  # drop "content/"
    segments = segments[:1]  # only 1 of depth
    return "/".join(segments + [self.fm.slug])


def Slugify(s: str) -> str:
  no_under = s.replace('_', ' ')
  lowercased = no_under.lower()
  segments = re.split("[^\w]+", lowercased)
  return ("-".join(segments)).strip('-')


def NoDiacriticsSlugify(s: str) -> str:
  return Slugify(unidecode.unidecode(s))


def DocumentFromPath(path: str, existing_paths: Set[str],
                     data_from_xml: Dict[str, MediawikiPage]) -> Optional[Document]:
  # First things first, let's check if we're even going to try.
  with open(path, "rb") as fd:
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


def WriteContent(content: str, path: str) -> None:
  backup_path = path + BACKUP_EXT
  if os.path.exists(backup_path):
    logging.warning("Won't write %r, because %r already exists",
                path, backup_path)
  # Let's not destroy people's work.
  shutil.copy(path, backup_path)
  with open(path, "wb") as fd:
    fd.write(content.encode("utf-8"))


def MarkdownPaths(dirname: str) -> Tuple[Set[str], Set[str]]:
  file_list = set()
  backup_file_list = set()
  for root, dirs, files in os.walk(dirname):
    for f in files:
      fullpath = os.path.join(root, f)
      if f.endswith('.md'):
        file_list.add(fullpath)
      if f.endswith('.md' + BACKUP_EXT):
        backup_file_list.add(fullpath)
  return file_list, backup_file_list


def DocumentsByPath(documents: Iterable[Document]) -> Dict[str, Document]:
  by_path: Dict[str, Document] = {}
  for doc in documents:
    assert doc.path not in by_path
    by_path[doc.path] = doc
    if doc.path.lower() not in by_path:
      by_path[doc.path.lower()] = doc
  return by_path


def _isNoteName(s: str) -> bool:
  flat = '♭'
  sharp = '♯'
  letters = [chr(x) for x in range(ord('A'), ord('H'))]
  with_flat = [x + flat for x in letters]
  with_sharp = [x + sharp for x in letters]
  notes = set(itertools.chain(letters, with_flat, with_sharp))
  return s in notes

def TitleFromPath(path: str) -> str:
  """Derive the title from path.

  content/książka/Foo.md => Foo

  Also support chord names with slashes:

  content/książka/F/C.md => F/C
  content/F/C.md => F/C
  """
  no_ext, _ = os.path.splitext(path)
  parts = no_ext.split("/")
  # Special case for chords.
  collapse_last_slash = _isNoteName(parts[-1])
  if collapse_last_slash:
    use_for_title = parts[-2] + '/' + parts[-1]
  else:
    use_for_title = parts[-1]
  return use_for_title.replace("_", " ")


if __name__ == '__main__':
  parser = argparse.ArgumentParser(
      description="Convert markdown from mediawiki-to-gfm to hugo.")
  parser.add_argument(
      "content_directory", metavar="PATH",
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
    "--xml-data", metavar="PATH", default=None,
    help="Path to the XML export from Mediawiki")
  args = parser.parse_args()
  CATEGORY_TAG = args.category_tag
  IMAGE_TAG = args.image_tag
  logging.basicConfig(level=logging.INFO)
  # TODO: Make this script work from other locations tool.
  assert args.content_directory == 'content', (
    "You need to be in Hugo root and use the argument 'content', e.g. "
    "python3 utils/mediawiki_markdown_to_hugo.py content"
  )
  markdown_paths, backup_paths = MarkdownPaths(args.content_directory)

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

  for backup_path in backup_paths:
    logging.debug("Backup file %s already exists; Restoring it automatially",
                  backup_path)
    path: str = re.sub('\.orig$', '', backup_path)
    shutil.move(backup_path, path)

  # Let's verify that backups have been restored.
  markdown_paths, backup_paths = MarkdownPaths(args.content_directory)
  assert not backup_paths

  documents: Dict[str, Document] = {}
  for path in markdown_paths:
    doc = DocumentFromPath(path, markdown_paths, data_from_xml)
    if doc is None:
      continue
    wiki_name = doc.fm.wiki_name
    assert wiki_name not in documents, (
      f"Page {wiki_name!r} ({doc.path}) is already in documents: "
      f"{documents[wiki_name].path}")
    documents[wiki_name] = doc

  redirects: Dict[str, str] = {}
  # Need to find the redirects, and assign aliases.
  for wiki_name, doc in documents.items():
    if doc.fm.redirect is None:
      continue
    if doc.fm.redirect in documents:
      documents[doc.fm.redirect].fm.aliases.append(doc.URLPath())
      # The target in the dictionary should be the path of the .md file.
      doc_dir, _ = os.path.split(doc.path)
      dest_path = os.path.join(doc_dir, doc.fm.redirect + ".md")
      redirects[doc.path] = dest_path
    elif re.match(':'+CATEGORY_TAG+':', doc.fm.redirect,
                     flags=re.IGNORECASE):
      # TODO: A redirection to a category page.
      logging.warning("%r tries to redirect to a category page: %r", doc.path, doc.fm.redirect)
    else:
      logging.warning(f"Bad redirect: {doc.fm.redirect!r}")

  # Now that we're unlinking documents, we need to replace the references to
  # redirects in existing documents. For each document, for each reference, N*M.
  for path, destination in redirects.items():
    for doc in documents.values():
      doc.content = doc.content.replace(path, dest_path)

  by_path: Dict[str, Document] = DocumentsByPath(documents.values())

  for doc in documents.values():
    updated_content: str = doc.fm.ToString() + (doc.RemoveCategoryLinks()
                          .HandleImageTags()
                          .TryToFixWikilinks(by_path, redirects)
                          .FixMonospace()
                          .content)

    WriteContent(updated_content, doc.path)

  for doc in documents.values():
    if doc.fm.redirect:
      logging.info("Unlinking %r because it's a redirection to %r", doc.path,
                   doc.fm.redirect)
