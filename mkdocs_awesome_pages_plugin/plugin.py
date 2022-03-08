from curses import meta
import warnings
import os
import pycond as pc
from typing import List, Dict
import re

from mkdocs.config import config_options, Config
from mkdocs.plugins import BasePlugin
from mkdocs.structure.files import Files, File
from mkdocs.structure.nav import (
    Navigation as MkDocsNavigation,
    get_navigation,
    Section,
    Link,
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
        regex_link = r"<\s*a\s+(?:(?:href=\"([\w\d\.]+)\"\s*)|([\w=]*\"([\w\d\.]+)\"\s*))+>"
        regex_image = r"!\[[\w\d]*\]\(([\w\/\.]+)\)"
        folders_to_clean = []
        not_md_to_keep = []
        to_removes = []
        for file in files:            
            if file.is_documentation_page():
                abs_path = file.abs_src_path
                filename = os.path.basename(abs_path).lower()
                dir = os.path.dirname(abs_path)
                meta = Meta.try_load_from(os.path.join(dir, ".pages"))
                if meta != None and meta.nav != None:
                    if meta.filter_not_referenced:
                        folders_to_clean.append(dir)
                    envs_meta = [env_meta for env_meta in meta.nav if isinstance(env_meta, MetaNavEnvCondition)]
                    for env_meta in envs_meta:
                        if env_meta.value.lower() == filename and not env_meta.is_valid():
                            to_removes.append(file)
                            break
        
        for to_remove in to_removes:
            files.remove(to_remove)
        
        to_removes = []

        for folder_to_clean in folders_to_clean:
            for file in files:
                if os.path.splitext(file.abs_src_path)[1] == ".md":
                    if  str(file.abs_src_path).startswith(folder_to_clean):
                        with open(file.abs_src_path) as f:
                            file_text = f.read()
                        file_dirname = os.path.dirname(file.abs_src_path)
                        for match in re.finditer(regex_image, file_text):
                            not_md_to_keep.append(os.path.join(file_dirname, match.groups()[0]))
                        for match in re.finditer(regex_link, file_text):
                            not_md_to_keep.append(os.path.join(file_dirname, match.groups()[0]))

        for folder_to_clean in folders_to_clean:
            for file in files:
                if os.path.splitext(file.abs_src_path)[1] != ".md" and str(file.abs_src_path).startswith(folder_to_clean) and not file.abs_src_path in not_md_to_keep:
                    if not file in to_removes:
                        to_removes.append(file)
        
        for to_remove in to_removes:
            print("Awesome_page: removed because not linked in filtered folder: " + to_remove.abs_src_path)
            files.remove(to_remove)


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
