from curses import meta
import math
import warnings
import os
import pycond as pc
from typing import List, Dict
import re

from mkdocs.config import config_options, Config
from mkdocs.plugins import BasePlugin
from mkdocs.structure.files import Files, File
from mkdocs.structure.pages import Page
from mkdocs.structure.nav import (
    Navigation as MkDocsNavigation,
    get_navigation,
    Section,
    Link
)
from mkdocs.structure.pages import Page

from .meta import DuplicateRestItemError, Meta, MetaNavEnvCondition, MetaNavRestItem, RestItemList
from .navigation import AwesomeNavigation, get_by_type, NavigationItem
from .options import Options


class NavPluginOrder(Warning):
    def __init__(self, plugin_name: str):
        super().__init__(
            'The plugin "{plugin_name}" might not work correctly when placed before awesome-pages in the list of '
            "plugins. It defines an on_nav handler that will be overridden by awesome-pages in some circumstances.".format(
                plugin_name=plugin_name
            )
        )


class AwesomePagesPlugin(BasePlugin):

    REFERENCED_FILES_EXCEPT_HTML = []
    FOLDERS_TO_CLEAN = []

    DEFAULT_META_FILENAME = ".pages"
    REST_PLACEHOLDER = "AWESOME_PAGES_REST"

    config_scheme = (
        ("filename", config_options.Type(str, default=DEFAULT_META_FILENAME)),
        ("collapse_single_pages", config_options.Type(bool, default=False)),
        ("strict", config_options.Type(bool, default=True))
    )

    def __init__(self):
        self.nav_config_with_rest = None
        self.rest_items = RestItemList()
        self.rest_blocks = {}
        for variable_name in os.environ.keys():
            print("Awesome_page: env var set " + variable_name)
            pc.State[variable_name] = " "

    def on_files(self, files: Files, config: Config):
        DELETED_FILES = []
        to_removes = []
        for file in files:        
            if file.is_documentation_page():
                abs_src_path = file.abs_src_path
                filename = os.path.basename(abs_src_path).lower()
                dir_src = os.path.dirname(abs_src_path)
                dir_dest = os.path.dirname(file.abs_dest_path)
                meta = Meta.try_load_from(os.path.join(dir_src, ".pages"))
                if meta != None and meta.nav != None:
                    if meta.filter_not_referenced:                        
                        if(dir_dest not in self.FOLDERS_TO_CLEAN):
                            self.FOLDERS_TO_CLEAN.append(dir_dest)
                    envs_meta = [env_meta for env_meta in meta.nav if isinstance(env_meta, MetaNavEnvCondition)]
                    for env_meta in envs_meta:
                        if env_meta.value.lower() == filename and not env_meta.is_valid():
                            env_meta.print_explaination()
                            to_removes.append(file)
                            break
        
        for to_remove in to_removes:
            files.remove(to_remove)
        
        AwesomeNavigation.DELETED_FILES.extend([to_remove.abs_src_path for to_remove in to_removes])

    def on_page_content(self, html: str, page: Page, config: Config, files: Files):
        #capture <a href="(path)">link name</a> or <img src="(path)"/>
        regex_link = r"<\s*(?:(?:a)|(?:img))\s+(?:(?:(?:(?:href)|(?:src))=\"([^\"]*\.[^\"]+)\"\s*)|(?:[\w=]*(?:\"(?:(?:(?:\\\")|(?:[^\"]))*)\")?\s*))+\/?>"
        found = False
        for folder_to_clean in self.FOLDERS_TO_CLEAN:
            if  str(page.file.abs_dest_path).startswith(folder_to_clean):
                found = True
                break
        if found:
            file_dirname = os.path.dirname(page.file.abs_dest_path)
            for match in re.finditer(regex_link, html):
                group = match.groups()[0]
                if group is not None :
                    if not group.lower().endswith(".html"):
                        print("Awesome_page: on_page_content catch " + group)
                        path = os.path.normpath(os.path.join(file_dirname, group))
                        self.REFERENCED_FILES_EXCEPT_HTML.append(path)

    def on_post_build(self, config: Config):
        to_removes = []
        for folder_to_clean in self.FOLDERS_TO_CLEAN:
            print("Awesome_page: post_build folder_to_clean " + folder_to_clean)
            to_ignores = [os.path.join(folder_to_clean, to_ignore) for to_ignore in ["assets", "search", "sitemap.xml", "sitemap.xml.gz"]]
            for source_dir, dirnames, filenames in os.walk(folder_to_clean, followlinks=True):
                is_to_ignore = False
                for to_ignore in to_ignores:
                        if source_dir.startswith(to_ignore):
                            is_to_ignore=True
                            break
                if is_to_ignore:
                        continue
                for filename in filenames:
                    if str(filename).lower().endswith(".html"):
                        continue
                    path = os.path.normpath(os.path.join(source_dir, filename))
                    is_to_ignore = False
                    for to_ignore in to_ignores:
                        if path.startswith(to_ignore):
                            is_to_ignore=True
                            break
                    if is_to_ignore:
                        continue
                    if path not in self.REFERENCED_FILES_EXCEPT_HTML:
                        if path not in to_removes:
                            to_removes.append(path)
        for to_remove in to_removes:
            print("Awesome_page: removed because not linked in filtered folder: " + to_remove)
            os.remove(to_remove)
            while len(os.listdir(os.path.dirname(to_remove))) == 0:
                to_remove = os.path.dirname(to_remove)
                os.rmdir(to_remove)


    def on_nav(self, nav: MkDocsNavigation, config: Config, files: Files):
        explicit_nav = nav if config["nav"] else None

        if self.nav_config_with_rest:
            # restore explicit config with rest placeholder and build nav
            config["nav"] = self.nav_config_with_rest
            explicit_nav = get_navigation(files, config)

        explicit_sections = set(get_by_type(explicit_nav, Section)) if explicit_nav else set()

        if self.nav_config_with_rest:
            self.rest_blocks = self._generate_rest_blocks(nav.items, [page.file for page in explicit_nav.pages])
            self._insert_rest(explicit_nav.items)
            nav = explicit_nav

        print(self.config)

        return AwesomeNavigation(nav.items, Options(**self.config), config["docs_dir"], explicit_sections).to_mkdocs()

    def on_config(self, config: Config):
        for name, plugin in config["plugins"].items():
            if name == "awesome-pages":
                break
            if hasattr(plugin, "on_nav"):
                warnings.warn(NavPluginOrder(name))

        if config["nav"]:
            self._find_rest(config["nav"])
            if self.rest_items:
                self.nav_config_with_rest = config["nav"]
                config["nav"] = None  # clear nav to prevent MkDocs from reporting files that are not included

        return config

    def _find_rest(self, config):
        if isinstance(config, list):
            for index, element in enumerate(config):
                if MetaNavRestItem.is_rest(element):
                    rest_item = MetaNavRestItem(element)
                    if rest_item in self.rest_items:
                        raise DuplicateRestItemError(rest_item.value, "mkdocs.yml")
                    self.rest_items.append(rest_item)

                    config[index] = {AwesomePagesPlugin.REST_PLACEHOLDER: "/" + element}
                else:
                    self._find_rest(element)

        elif isinstance(config, dict):
            for value in config.values():
                self._find_rest(value)

    def _generate_rest_blocks(
        self, items: List[NavigationItem], exclude_files: List[File]
    ) -> Dict[str, List[NavigationItem]]:
        result = {rest_item: [] for rest_item in self.rest_items}
        for item in items[:]:  # loop over a shallow copy of items so removing items doesn't break iteration
            if isinstance(item, Page):
                if item.file not in exclude_files:
                    for rest_item in self.rest_items:
                        if rest_item.matches(item.file.src_path):
                            items.remove(item)
                            result[rest_item].append(item)
                            break
            if isinstance(item, Section):
                child_result = self._generate_rest_blocks(item.children, exclude_files)
                for rest_item, children in child_result.items():
                    if children:
                        if rest_item.flat:
                            result[rest_item].extend(children)
                        else:
                            result[rest_item].append(Section(item.title, children))
        return result

    def _insert_rest(self, items):
        for index, item in enumerate(items):
            if isinstance(item, Link) and item.title == AwesomePagesPlugin.REST_PLACEHOLDER:
                items[index : index + 1] = self.rest_blocks[MetaNavRestItem(item.url[1:])]
            if isinstance(item, Section):
                self._insert_rest(item.children)
