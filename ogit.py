#!/usr/bin/env python3
# pip install bs4 curlify websocket-client gitpython
## TODOs:
# - clarify info given by logger.info (no information about merge for example)
# - write the cli
# - improve configurability

from bs4 import BeautifulSoup
import json
import requests
# import cookielib
from http.cookiejar import CookieJar
import curlify
from getpass import getpass
import os
from websocket import create_connection # websocket-client
import logging
import zipfile
import ntpath
from pathlib import Path
from datetime import datetime
import git
import shutil
from distutils.dir_util import copy_tree
import subprocess
import sys
import argparse

##############################
### Logger
##############################

## Add level logging.SPAM
logging.SPAM = 5
logging.addLevelName(logging.SPAM, "SPAM")
def spam(self, message, *args, **kws):
    if self.isEnabledFor(logging.SPAM):
        self._log(logging.SPAM, message, args, **kws)
logging.Logger.spam = spam
## Configure logger
logger = logging.getLogger('root')
FORMAT = "[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
logging.basicConfig(format=FORMAT)
# logger.setLevel(logging.INFO)
# logger.setLevel(logging.DEBUG)
logger.setLevel(logging.SPAM)

##############################
### Constants
##############################

GIT_OVERLEAF_BRANCH="overleaf"

##############################
### Exceptions
##############################

class OverleafException(Exception):
    """All the exceptions run in this project should derive from
    this exception."""
    pass

class ConnectException(OverleafException):
    """Problem during the connection that is not really known."""
    pass

class BadUserOrPassword(ConnectException):
    """Bad username or password"""
    pass

class ExplicitConnectException(ConnectException):
    """Problem during the connection with an explicit
    error message"""
    pass

class GetZipError(OverleafException):
    """Any error during getting the zip"""
    pass

class BadZip(GetZipError):
    """The zip file is not openable"""
    pass

class ErrorDuringGetListFilesFolders(OverleafException):
    """An error occured during the process of getting the list
    of files/folders"""
    pass

class BadFormatJsonListFilesFolders(ErrorDuringGetListFilesFolders):
    """If the json received to get the list of files and folders
    does not have the good shape"""
    pass

class ErrorUploadFile(OverleafException):
    """This error is raised when an error occurs during
    a file upload"""
    pass

class BadJsonFormat(OverleafException):
    """This generic error is raised when the Json does not have the good shape."""

class PathExistsButIsFile(OverleafException):
    """Error raised when trying to create a new folder but a file already exists there."""

class FileDoesNotExist(OverleafException):
    """Generic error when a file does not exist"""

class FileDoesNotExistSoNoRemove(FileDoesNotExist):
    """When you try to remove a file that does not exist."""

class FileDoesNotExistSoNoMove(FileDoesNotExist):
    """When you try to move a file that does not exist."""

class DstFolderDoesNotExistSoNoMove(FileDoesNotExist):
    """When you try to move a file to a folder that does not exist."""

class DstFolderIsFile(FileDoesNotExist):
    """The dst folder is a file!"""

class FileErasureNotAllowed(OverleafException):
    """We need to erase a file and we are not 'allowed' (in parameters) to erase stuff."""

class ImpossibleError(OverleafException):
    """This class of error are raised when an error should not occur. For example mkdir with force=True should create a folder..."""


class GitException(OverleafException):
    """All exception linked with git"""

class NoGitRepo(GitException):
    """When no git repo exists."""

class BareRepoNotSupported(GitException):
    """When no git repo exists."""

class NoOverleafBranchExists(GitException):
    """If no overleaf branch exists."""

class RunsInOgitRepo(GitException):
    """When the user forgot to change folder, he will run
    the script in the git folder of ogit... Which is usually
    not what he wants to do."""

class ErrorDuringMerge(GitException):
    """When an error occurs during the merge (merge conflict...)"""

class DirtyRepository(GitException):
    """When an error occurs during the merge (merge conflict...)"""

class GitRepoAlreadyExist(GitException):
    """Run this error during cloning if a repo already exists."""

class ProjectConfException(OverleafException):
    """Run this error during cloning if a repo already exists."""

class ProjectConfAlreadExist(ProjectConfException):
    """Run this error during cloning if a repo already exists."""


##############################
### Way to represent a file/folder in overleaf
##############################

class FileTree:
    """Represents a file/folder tree on overleaf's website.
    We register here the file/folder ids, as well as the
    """
    def __init__(self):
        self.l = dict()

    def get_canon_path(self, path_name, should_finish_slash=False):
        """Depending on the context, path should have a trailing slash (in ['path']) or not (in key). Get the canonical version of a path!"""
        # Path should start and end with /
        if len(path_name) == 0 or path_name == "/":
            return "/"
        else:
            if should_finish_slash:
               if path_name[-1] != "/":
                   path_name += "/"
            else:
                # No / at the end of folders
                if path_name[-1] == "/":
                    path_name = path_name[:-1]
            # path starts with /
            if path_name[0] != "/":
                path_name = "/" + path_name
            return path_name

    def add_element(self, name, path, _id, file_type, parent_id=None):
        """a path starts with a slash and ends with a slash.
        file_type can be either folder, file, or doc.
        Parent_id should be None for root folders."""
        path = self.get_canon_path(path, should_finish_slash=True)
        # name should not contain '/'
        name = name.replace("/", "")
        self.l[path + name] = {
            'name': name,
            'path': path,
            '_id': _id,
            'file_type': file_type,
            'parent_id': parent_id}

    def get_element(self, path_name):
        """Get the element corresponding to the given path name.
        In case the element does not exist, return None.
        In case the element exists, return a dict containing
        the name, the path, the _id, file_type (folder, file, or doc), and parent_id."""
        path_name = self.get_canon_path(path_name,
                                        should_finish_slash=False)
        try:
            return self.l[path_name]
        except KeyError:
            return None

    def remove_element(self, path_name, no_error=True):
        path_name = self.get_canon_path(path_name)
        if no_error:
            self.l.pop(path_name, True)
        else:
            self.l.pop(path_name)

    def get_list_files(self):
        return [filename
                for filename,elt in self.l.items()
                if elt['file_type'] != 'folder']
            
    def get_list_folders(self):
        return [filename
                for filename,elt in self.l.items()
                if elt['file_type'] == 'folder']

    def __str__(self):
        s = ""
        for path_name, d in self.l.items():
            s += path_name + (" (File "
                              if d['file_type'] != 'folder'
                              else " (Dir ") + d['_id'] +  ")\n"
        return s

##############################
### Class that deals with the overleaf website
##############################

class Overleaf:
    """This class will be the one interacting with the
    overleaf online's website."""
    def __init__(self, url_project=None, email=None, password=None):
        self.email = email or os.environ.get("OVERLEAF_EMAIL") or input("email? ")
        self.password = password or os.environ.get("OVERLEAF_PASSWORD") or getpass("password? ")
        self.old_overleaf_session = None
        self.overleaf_session = None
        self.csrf_token = None
        self.file_tree = None
        self.url_project = url_project or os.environ.get("URL_PROJECT") or input("What is the url of the project?")
        if self.url_project[-1] != "/":
            self.url_project = self.url_project + "/"
        else:
            self.url_project = self.url_project
        self.project_id = self.url_project.split("/")[-2]
        self._connect() # sets old_overleaf_session, csrf_token, and session

    def _connect(self):
        # Go to the login page
        self.session = requests.Session()
        logger.info('#### Trying to connect...')
        try:
            logger.debug('## 1) Go to login page')
            # Note the self.session instead of requests.
            r = self.session.get('https://www.overleaf.com/login')
            soup = BeautifulSoup(r.text, "html.parser")
            self.csrf_token = soup.find('input', {'name':'_csrf'})['value']
            self.old_overleaf_session = r.cookies["overleaf_session"]
            print("All cookies: {}".format(self.session.cookies))
            
            logger.debug('The old overleaf session is {}'.format(self.old_overleaf_session))
            logger.debug('The csrf token is {}'.format(self.csrf_token))
            # Send the login informations
            logger.debug('## 2) Send the email/passwd informations')
            # self.jar = cookielib.CookieJar()
            r = self.session.post('https://www.overleaf.com/login',
                      # cookies = {'overleaf_session': self.old_overleaf_session},
                      headers = {'Content-Type': 'application/json;charset=UTF-8',
                                 'Accept': 'application/json, text/plain, */*'},
                      json = {'_csrf': self.csrf_token,
                              'email': self.email,
                              'password': self.password})
            # print(r.headers)
            # print(requests.MockRequest(r).get_new_headers().get('Cookie'))
            # /!\ Very dirty, does not handle string cookies...
            print(r.cookies)
            self.cookie_string = " ".join(
                [ "{}:{};".format(cookie,
                                  self.session.cookies[cookie])
                  for cookie in self.session.cookies.get_dict()]
            )
            print("cookie_string: {}".format(self.cookie_string))
            out_json = r.json()
            # Sanity checks, handle errors
            if not "redir" in out_json:
                logger.warning("The json does not contain the good informations: {}".format(out_json))
                
                try:
                    if out_json["message"]["type"] == "error":
                        txt = out_json["message"]["text"]
                        if "your email or password is incorrect." in txt.lower():
                            raise BadUserOrPassword(txt)
                        raise ExplicitConnectException(txt)
                    else:
                        raise ConnectException(out_json)
                except KeyError:
                    raise ConnectException(out_json)
            self.overleaf_session = r.cookies["overleaf_session"]
            # New cookie gke-route. Google kubernete routing?
            # self.gke_route = r.cookies["gke-route"]
            logger.debug("### Coockies: {}".format(r.cookies))
            logger.debug("The good overleaf session is {}".format(self.overleaf_session))
            if self.overleaf_session[0:2] != "s%":
                logger.warning("The overleaf session does not start with s%, which is quite unusual...")
        except OverleafException:
            raise
        except Exception as e:
            raise ConnectException(e) from e
        
    def get_zip(self, outputfile="output_ogit.zip"):
        """Get the zip file associated with a given project"""
        try:
            logger.info('#### Getting the zip for project {}'.format(self.url_project))
            r = self.session.get('{}download/zip'.format(self.url_project),
                             # cookies = {'overleaf_session': self.overleaf_session}
            )
            with open(outputfile, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024): 
                    if chunk:
                        f.write(chunk)
        except Exception as e:
            raise GetZipError(e) from e
        if not zipfile.is_zipfile(outputfile):
            err = "The output file {} is not a zip file.".format(outputfile)
            logger.error(err)
            raise BadZip(err)

    def ls(self, force_reload=True):
        """This function gets the list of files and folders on the
        online overleaf website, and sets self.file_tree to the associated
        file tree.
        """
        if self.file_tree and not force_reload:
            logger.debug("The file tree already exists, and we don't force to reload, so I'll provide the same file_tree as before")
            logger.spam("{}".format(self.file_tree))
            return self.file_tree
        logger.info("#### Getting list of files and folders of the project {}".format(self.url_project))
        try:
            print("Jar: {}".format(self.session.cookies))
            r = self.session.get('https://www.overleaf.com/socket.io/1/',
                                 params = {'t': 1568856483734},
                                 
                             # cookies = {'overleaf_session': self.overleaf_session,
                                        # 'SERVERID': 'sl-lin-prod-web-5'}
            )
            logger.debug(curlify.to_curl(r.request))
            logger.debug("request to get io: {}".format(r.text))
            socket_url = r.text.split(':')[0]
            full_wsurl = "wss://www.overleaf.com/socket.io/1/websocket/{}".format(socket_url)
            logger.debug("full_wsurl: {}".format(full_wsurl))
            # ws_cookie = "SERVERID=sl-lin-prod-web-5;gke-route={};overleaf_session={};".format(self.gke_route, self.overleaf_session)
            # logger.debug("websocat {} -H \'Cookie: {}\'".format(full_wsurl, ws_cookie))
            ws_cookie = self.cookie_string
            logger.debug("websocat {} -H \'Cookie: {}\'".format(full_wsurl, ws_cookie))
            # exit(1)
            ws = create_connection(full_wsurl,
                                   extra_headers=[('Cookie', ws_cookie)])
                                   # cookie = self.session.cookies)
                                   # cookie = ws_cookie)
            out_json = None
            while True:
                logger.debug("Waiting to receive a message...")
                resp = ws.recv()
                logger.debug("I received: {}".format(resp))
                if resp[0] =='5':
                    to_send = '5:1+::{"name":"joinProject","args":[{"project_id":"' + self.project_id + '"}]}'
                    logger.debug("I'll send:{}".format(to_send))
                    ws.send(to_send)
                if resp[0] ==  '6':
                    logger.debug("I will jsonize: {}".format(resp[6:]))
                    out_json = json.loads(resp[6:])
                    break
            logger.debug("Json: {}".format(out_json))
        except Exception as e:
            raise ErrorDuringGetListFilesFolders(e) from e
        try:
            self.name_project = out_json[1]['name']
            rootFolderJson = out_json[1]['rootFolder'][0]
            rootFolderJson['name'] = ''
            ft = FileTree()
            def iterate_folder(json_folders, path="/", parent_id=None):
                name = json_folders['name']
                current_folder_id = json_folders['_id']
                ft.add_element(name=name,
                               path = path,
                               _id = current_folder_id,
                               file_type = "folder",
                               parent_id = parent_id)
                # Add the subfolders
                if name == "":
                    newpath = path
                else:
                    newpath = path + name + "/"
                for json_subfolders in json_folders['folders']:
                    iterate_folder(json_subfolders,
                                   path=newpath,
                                   parent_id=json_folders['_id'])
                # Add the files in "docs"
                for doc in json_folders['docs']:
                    ft.add_element(name = doc['name'],
                                   path = newpath,
                                   _id = doc['_id'],
                                   file_type = 'doc',
                                   parent_id = current_folder_id)
                for doc in json_folders['fileRefs']:
                    ft.add_element(name = doc['name'],
                                   path = newpath,
                                   _id = doc['_id'],
                                   file_type = 'file',
                                   parent_id = current_folder_id)
            iterate_folder(rootFolderJson)
            self.file_tree = ft
            logger.debug(ft)
            return ft
        except Exception as e:
            raise BadFormatJsonListFilesFolders(e) from e

    def rm(self, path_name=None, _id_filetype=None, force=False, force_reload=True):
        """Remove a file at the given path. Specify either the path or a couple (_id,file_type).
        If force=True, then do not raise an error if the file does not exist."""
        if _id_filetype:
            (_id,file_type) = _id_filetype
        else:
            ft = self.ls(force_reload=force_reload)
            try:
                elt = ft.get_element(path_name) or dict()
                _id = elt['_id']
                file_type = elt['file_type']
            except KeyError as e:
                logger.debug("The file {} does not exist and you try to remove it...".format(path_name or _id))
                if force:
                    return None
                else:
                    raise FileDoesNotExistSoNoRemove(e) from e
        logger.debug("I will delete id {}.".format(_id))
        mid_url = elt['file_type'] + "/"
        r = self.session.delete("{}{}{}".format(self.url_project,
                                            mid_url,
                                            _id),
                            # cookies = {'overleaf_session': self.overleaf_session},
                            headers = {'Accept': 'application/json, text/plain, */*',
                                       'X-Csrf-Token': self.csrf_token})
        logger.debug(curlify.to_curl(r.request))
        self.file_tree.remove_element(path_name)
        logger.debug(r.text)


    def mkdir(self, online_path, force=False, force_reload=True, nb_retry=1):
        """Create (recursively if needed) a folder online.
        if force=True, will remove any existing file that would be in place of the folder."""
        subfolders = [p for p in online_path.split('/') if p]
        ft = self.ls(force_reload=force_reload)
        path="/"
        for p in subfolders:
            new_path = path + p + "/"
            logger.debug("Will deal with subpath {}.".format(new_path))
            elt = ft.get_element(new_path)
            if elt:
                if elt['file_type'] == 'folder':
                    logger.debug("The folder {} already exist.".format(new_path))
                else:
                    logger.debug("The path {} already exist BUT IS A FILE.".format(new_path))
                    if not force:
                        raise PathExistsButIsFile
                    else:
                        self.rm(_id_isfile = (elt['_id'], True), force=True, force_reload=force_reload)
                        self.mkdir(online_path,
                                   force=force,
                                   force_reload=force_reload)
            else:
                logger.debug("The folder {} does not exist. Let's create it!".format(new_path))
                parent_id=ft.get_element(path)['_id']
                r = self.session.post(self.url_project + 'folder',
                                      # cookies = {'overleaf_session': self.overleaf_session},
                                      headers = {'Content-Type': 'application/json;charset=UTF-8',
                                                 'Accept': 'application/json, text/plain, */*'},
                                      json = {'_csrf': self.csrf_token,
                                              'parent_folder_id': parent_id,
                                              'name': p})
                logger.debug(curlify.to_curl(r.request))
                if "file already exists" in r.text:
                    ### If the file already exist, it means that the file has been created meanwhile, so let's try again! (NB: that is quite unlikely to happen when force_reload=False)
                    if nb_retry <= 0:
                        logger.warning("The file {} already exists online, but wasn't on the file tree after several tries... That's REALLY strange, so if you see this warning, please do a report!")
                        return
                    logger.warning("The file {} already exists online, but wasn't on the file tree... That's strange, let's try again! Note that this should NOT loop, else please do a bug report.")
                    self.ls(force_reload=True)
                    self.mkdir(online_path,
                               force=force,
                               force_reload=force_reload,
                               nb_retry=nb_retry-1)
                try:
                    logger.spam(r.text)
                    out_json = r.json()
                    logger.debug(out_json)
                    new_id = out_json["_id"]
                    ft.add_element(p,
                                   path=path,
                                   _id=new_id,
                                   file_type='folder',
                                   parent_id=parent_id)
                except Exception as e:
                    raise BadJsonFormat(e) from e
            path = new_path

    def mv(self, src, dst_folder, new_name=None, create_folder=False, allow_erase=False, force=False, force_reload=True, nb_retry=1):
        """Move src to dst_folder, and eventually change
        the name to new_name. It can move both files and folders.
        If force=True, do not raise an error even if the input file does not exist"""
        ### Sorry the code of this function is ugly, but tries to deal with as many case/errors
        ### as possible... (and I didn't think it would be that complex before actually writing it)
        ### If I've the time I may try to rewrite it in a better way
        logger.debug("I will try to move file {} to folder {}{}.".format(src, dst_folder, "with name " + new_name if new_name else ""))
        ft = self.ls(force_reload=force_reload)
        src_elt = ft.get_element(src)
        if not src_elt:
            # The src file does not exist...
            if nb_retry <= 0 or force_reload == True:
                logger.error("Cannot move the file {}, it does not exist.".format(src))
                if force:
                    return
                raise FileDoesNotExistSoNoMove(src)
            logger.error("The file seems to be non-existant. Let's reload and try again.")
            self.ls(force_reload = True)
            self.mv(src,
                    dst_folder,
                    new_name,
                    create_folder=create_folder,
                    force_reload=force_reload,
                    nb_retry=nb_retry-1)
        mid_url = src_elt['file_type'] + "/"
        # Make sure that new_name is setup only if we really change the folder name
        if new_name == src_elt['name']:
            new_name = None
        # Deal with destination folder
        dst_elt = ft.get_element(dst_folder)
        if not dst_elt:
            logger.warning("The dst folder {} does not exist. ".format(dst_folder))
            # The dst folder does not exist...
            if not create_folder:
                raise DstFolderDoesNotExistSoNoMove(dst_folder)
            self.mkdir(dst_folder,
                       force=True,
                       force_reload=force_reload)
            dst_elt = ft.get_element(dst_folder)
            if not dst_elt:
                logger.error("Mkdir failed! Please do a bug report.")
                raise ImpossibleError("Mkdir didn't create the file, please do a but report.")
        if dst_elt['file_type'] != "folder":
            # The dst is a file instead of a folder
            logger.warning('The dst {} you are trying to move to is a file, not a folder!'.format(dst_folder))
            if not allow_erase:
                raise DstFolderIsFile(dst_folder)
            logger.warning('We will erase this file and create another one instead...')
            self.rm(dst_folder, force=True, force_reload=True)
            self.mkdir(dst_folder,
                       force=True,
                       force_reload=force_reload)
            dst_elt = ft.get_element(dst_folder)
            if not dst_elt or dst_elt['file_type'] != 'folder':
                logger.error("rm/mkdir failed! Please do a bug report.")
                raise ImpossibleError("rm or mkdir didn't create the file, please do a but report.")
        # Check if the destination file is not the same file (else do nothing)
        dst_folder_canon = self.file_tree.get_canon_path(dst_folder, should_finish_slash=True)
        final_dst = dst_folder_canon + (new_name or src_elt['name'])
        if src == final_dst:
            logger.debug("The source and destination are equal.")
            # The move does not move in fact
            return None
        ##### Check if we need to remove the output file before moving
        remove_later = False
        if self.file_tree.get_element(final_dst):
            logger.debug("File {} exists, so an erasure would be needed.".format(final_dst))
            # We need to remove the file
            if not allow_erase:
                raise FileErasureNotAllowed("File {} already exists.".format(final_dst))
            if not new_name:
                # we can remove the file now
                self.rm(final_dst, force=True, force_reload=force_reload)
            else:
                remove_later = True
        ##########################
        #### Move file if needed
        src_path_no_slash, src_filename = ntpath.split(src)
        src_path_slash = src_path_no_slash + "/" if len(src) == 0 or src_path_no_slash[-1] != "/" else src_path_no_slash
        # If the destination of the intermediate move already exists, we don't want to erase it
        # so we will first rename the file by prepending a prefix, and then move it.
        prefix = ""
        if dst_folder_canon != src_path_slash:
            # We need to do an intermediate move first
            logger.debug("We will first do an intermediave move")
            ##### Check if the intermediate move won't remove another file
            prefix_int = 0
            while (self.file_tree.get_element(dst_folder_canon + prefix + src_elt['name'])
                   or (prefix != "" and self.file_tree.get_element(src_path_slash + prefix + src_elt['name']))):
                logger.debug("An element already exists with prefix '{}' and name {}, either in folder {} or {}. Let's change the prefix:".format(prefix, src_elt['name'], dst_folder_canon, src_path_slash))
                logger.debug(self.file_tree)
                prefix_int += 1
                prefix = str(prefix_int) + "_"
            ##########################
            #### Pre-rename the file if needed
            if prefix:
                logger.debug("We will now pre-rename the file {} with the prefix {}.".format(src, prefix))
                url = '{}{}{}/rename'.format(self.url_project,
                                             mid_url,
                                             src_elt['_id'])
                r = self.session.post(url,
                                      # cookies = {'overleaf_session': self.overleaf_session},
                                      headers = {'Content-Type': 'application/json;charset=UTF-8',
                                                 'Accept': 'application/json, text/plain, */*'},
                                      json = {'name': prefix + src_elt['name'],
                                              '_csrf': self.csrf_token})
                if r.text:
                    # Error, as r.text should output nothing
                    logger.debug(curlify.to_curl(r.request))
                    logger.warning("Very strange, the rename request shouldn't  output anything!")
                    logger.debug(r.text)
                    raise ImpossibleError(r)
                if force_reload:
                    self.ls(force_reload=True)
                else:
                    self.file_tree.add_element(name=prefix + src_elt['name'],
                                               path=src_path_slash,
                                               _id=src_elt['_id'],
                                               file_type=src_elt['file_type'],
                                               parent_id=src_elt['parent_id'])
                    self.file_tree.remove_element(src)
                logger.debug("pre-renaming finished.")
            ##### Move the file to the folder (cannot change the name)
            logger.debug("I will move the file {} to the folder {}".format(src_path_slash + prefix + src_elt['name'], dst_folder))
            url = '{}{}{}/move'.format(self.url_project,
                                       mid_url,
                                       src_elt['_id'])
            r = self.session.post(url,
                                  # cookies = {'overleaf_session': self.overleaf_session},
                                  headers = {'Content-Type': 'application/json;charset=UTF-8',
                                         'Accept': 'application/json, text/plain, */*'},
                                  json = {'folder_id': dst_elt['_id'],
                                          '_csrf': self.csrf_token})
            if r.text:
                # Error, as r.text should output nothing
                logger.debug(curlify.to_curl(r.request))
                logger.warning("Very strange, the move request shouldn't  output anything!")
                logger.debug(r.text)
                raise ImpossibleError(r)
            if force_reload:
                self.ls(force_reload=True)
            else:
                self.file_tree.add_element(name=prefix + src_elt['name'],
                                           path=dst_folder,
                                           _id=src_elt['_id'],
                                           file_type=src_elt['file_type'],
                                           parent_id=dst_elt['_id'])
                self.file_tree.remove_element(src_path_slash + prefix + src_elt['name'])
        ##########################
        #### Rename file if needed
        if new_name and new_name != src_filename:
            logger.debug("We will now rename the file")
            if remove_later:
                self.rm(final_dst, force=True, force_reload=force_reload)
            url = '{}{}{}/rename'.format(self.url_project,
                                         mid_url,
                                         src_elt['_id'])
            r = self.session.post(url,
                                  # cookies = {'overleaf_session': self.overleaf_session},
                                  headers = {'Content-Type': 'application/json;charset=UTF-8',
                                         'Accept': 'application/json, text/plain, */*'},
                                  json = {'name': new_name,
                                          '_csrf': self.csrf_token})
            if r.text:
                # Error, as r.text should output nothing
                logger.debug(curlify.to_curl(r.request))
                logger.warning("Very strange, the rename request shouldn't  output anything!")
                logger.debug(r.text)
                raise ImpossibleError(r)
            if force_reload:
                self.ls(force_reload=True)
            else:
                self.file_tree.add_element(name=new_name,
                                           path=dst_folder,
                                           _id=src_elt['_id'],
                                           file_type=src_elt['file_type'],
                                           parent_id=dst_elt['_id'])
                self.file_tree.remove_element(dst_folder_canon + prefix + src_elt['name'])
        logger.info("### File {} has been moved successfully to folder {}{}".format(src, dst_folder, "and renamed to " + new_name if new_name else ""))

    def upload_file(self, online_path_name, local_path_name=None, string_content=None, force=False, force_reload=True):
        """Upload of file located on local_path_name on the online path online_path_name. If force==True, create the folder brutally by erasing any exising file/folder.
        If no local_path_name is provided, then send the content of "string" as file.
        """
        ft = self.ls(force_reload=force_reload)
        if online_path_name[0] != "/":
            online_path_name = "/" + online_path_name
        online_path, online_filename = ntpath.split(online_path_name)
        logger.debug("online_path: {}".format(online_path))
        logger.debug("online_filename: {}".format(online_filename))
        if not online_filename:
            raise ErrorUploadFile("The online path {} does not have a valid filename.".format(online_path_name))
        path_id = ft.get_element(online_path)
        if not path_id:
            # If the folder does not exist, create it
            self.mkdir(online_path,
                       force=force,
                       force_reload=force_reload)
            path_id = ft.get_element(online_path)
            if not path_id or path_id['file_type'] != 'folder':
                raise ImpossibleError("Mkdir didn't created a folder at {}, please report the error!".format(online_path_name))
        logger.debug("path_id: {}".format(path_id))
        # Upload the file
        content = open(local_path_name, 'rb') if local_path_name else string_content
        r = self.session.post("{}upload?folder_id={}&_csrf={}".format(self.url_project, path_id['_id'], self.csrf_token),
                              # cookies = {'overleaf_session': self.overleaf_session},
                              files = {'qqfile': (online_filename, content)})
        logger.debug(curlify.to_curl(r.request))
        logger.debug(r.text)
        try:
            out_json = r.json()
            if not out_json['success']:
                logger.debug("An unknown error occured during uploading. Please fill a bug report.")
                raise ErrorUploadFile(r.text)
            new_id = out_json['entity_id']
            file_type = out_json['entity_type']
            if force_reload:
                self.ls()
            else:
                self.file_tree.add_element(name=online_filename,
                                           path=online_path,
                                           _id=new_id,
                                           file_type=file_type,
                                           parent_id=path_id['_id'])
            logger.info("### Successful upload of file {} at online path {}.".format(local_path_name, online_path_name))
        except KeyError as e:
            logger.debug("An unknown error occured during uploading. Please fill a bug report.")
            raise ErrorUploadFile(r.text) from e

def demo_overleaf():
    o = Overleaf()
    # o.get_zip()
    # o.ls()
    # o.mkdir("/ogit/script/")
    # o.mkdir("/aa.txt")
    # o.rm("/bb.txt")
    # o.mv("/name.tex", "/myfolder3/script/")
    # o.mv("/myfolder3/script/", "/")
    # print(o.ls())
    # print("Let's start to play :D")
    # o.mv("/name.tex", "/myfolder3/script/", force_reload=False)
    # o.mv("/myfolder3/script/name.tex", "/", force_reload=False)
    # o.mv("/myfolder3/script/cren.zip", "/", force_reload=False)
    # o.mv("/cren.zip", "/myfolder3/script/", new_name="cren_rename_ogit.zip", force_reload=False)
    # o.mv("/othermain.tex", "/myfolder3/script/", new_name="fichier.txt", force_reload=False, allow_erase=True)
    # o.upload_file("/montest/fichier.txt", "/tmp/a.txt", force_reload=False)
    # o.mv("/fichier.txt", "/myfolder3/script/", new_name="fichier.txt", force_reload=False, allow_erase=True)
    # o.mv("/fichier.tex", "/myfolder3/script/", force_reload=False, allow_erase=True)
    # o.mv("/myfolder3/script/fichier.tex", "", force_reload=False, allow_erase=True)
    # o.mv("/fichier.tex", "/myfolder3/script/", new_name="fichiermoved.tex", force_reload=False, allow_erase=True)
    # o.mv("/fichiermoved.tex", "/myfolder3/script/", new_name="fichierrenamed.tex", force_reload=False, allow_erase=True)
    # print(o.ls())
    # o.upload_file("/ogitupload/fichier.txt", string_content="I'm a content completely written in python!", force_reload=False)
# demo_overleaf()

##############################
### Configuration project
##############################

class ConfProject:
    def __init__(self,
                 conf_dict=None,
                 json_string=None,
                 json_file=None,
                 try_to_find_conf=True,
                 url_project=None, email=None, password=None,
                 args=None):
        """You can either provide nothing and wait for the prompt (or use the environment variables), or give a dictionnary, or give a json string, or give a json filename, or give manually the 3 mandatory parameters. Or directly the args from the command line.
        The dict/json/... have:
        mandatory:
        - url_project
        - email
        - password
        facultative:
        - TODO: fill
        """
        self.conf_dict = conf_dict
        # If provide json string
        if not self.conf_dict and json_string:
            self.conf_dict = json.loads(json_string)
        # If provide json filename
        if not self.conf_dict and json_file:
            with open(json_file) as f:
                self.conf_dict = json.load(f)
        # Try to get the configuration file directly
        if not self.conf_dict and try_to_find_conf:
            try:
                repo = git.Repo(".")
                path_to_look = os.path.join(repo.working_tree_dir, ".ogit_confproject")
            except git.InvalidGitRepositoryError:
                path_to_look = ".ogit_confproject"
            if os.path.isfile(path_to_look):
                with open(path_to_look) as f:
                    self.conf_dict = json.load(f)
        if not self.conf_dict:
            self.conf_dict = dict()
        # Load project url
        self.url_project = url_project or self.conf_dict.get('url_project') or os.environ.get("URL_PROJECT") or input("What is the url of the project? ")
        self.email = email or self.conf_dict.get('email') or os.environ.get("OVERLEAF_EMAIL") or input("email? ")
        self.password = password or self.conf_dict.get('password') or os.environ.get("OVERLEAF_PASSWORD") or getpass("password? ")
        self.conf_dict['url_project'] = self.url_project
        self.conf_dict['email'] = self.email
        self.conf_dict['password'] = self.password

    def get_url_project(self):
        return self.url_project

    def get_email(self):
        return self.email

    def get_password(self):
        return self.password

    def have_svg(self):
        return self.conf_dict.get('have_svg', True)

    def get_svg_path(self):
        return self.conf_dict.get('svg_path', '.ogit_svg')

    def get_overleaf_branch_name(self):
        return self.conf_dict.get('overleaf_branch_name', 'overleaf')

    def get_force_reload(self):
        return self.conf_dict.get('ls_force_reload', False)

    def get_overleaf(self):
        return Overleaf(
            url_project=self.get_url_project(),
            email=self.get_email(),
            password=self.get_password()
        )

    def get_path_to_save(self):
        try:
            repo = git.Repo(".")
            logger.debug("We are in a repository")
            return os.path.join(repo.working_tree_dir, ".ogit_confproject")
        except git.InvalidGitRepositoryError:
            logger.debug("We are not in a repository")
            return ".ogit_confproject"

    def save(self, outfile=None):
        """This function save the confproject into a file (as json)"""
        outfile = outfile or self.get_path_to_save()
        logger.debug("Will save dict {}".format(self.conf_dict))
        with open(outfile, 'w', encoding='utf-8') as outfile:
            json.dump(self.conf_dict, outfile)
        logger.info("Configuration file saved in {}".format(outfile))

##############################
### Git integration
##############################

def run_interactive_command(args):
    """Args is a list of arguments (including the program name), and we will plug this command into git."""
    return subprocess.call(args, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)

def get_repo():
    cwd = os.getcwd()
    try:
        repo = git.Repo(cwd, search_parent_directories=True)
        logger.debug("I found a repo in {} whose working dir is {}.".format(cwd, repo.working_tree_dir))
        if repo.bare:
            raise BareRepoNotSupported()
        return repo
    except git.InvalidGitRepositoryError:
        raise NoGitRepo("No git repository found in {}".format(cwd))

def overleaf_branch_exists():
    """Return True if the overleaf branch actually exist"""
    repo = get_repo()
    return GIT_OVERLEAF_BRANCH in [b.name for b in repo.branchs]

class OverleafRepo():
    """This class needs to be used with the 'with' keyword
    to make sure that the repository is sent back to it's
    original state. Should use it like:
    with OverleafRepo(repo) as repo:
    """
    def __init__(self, repo=None, confproject=None, overleaf_branch=GIT_OVERLEAF_BRANCH, warning_run_in_ogit_folder=True):
        self.repo = repo or get_repo()
        self.overleaf_branch = confproject.get_overleaf_branch_name() if confproject else overleaf_branch
        if warning_run_in_ogit_folder and os.path.exists(
                os.path.join(self.repo.working_tree_dir,
                             'should_not_run_ogit_here.txt')):
            print("WARNING:")
            print("It seems that you are running ogit in the git")
            print("repository of ogit, while usually you should")
            print("create an other git repo, and call ogit from")
            print("this other git repo.")
            yes_no = input("Are you sure you want to continue?[y/N]")
            if yes_no.lower() not in ["y", "yes"]:
                raise RunsInOgitRepo()

    def __enter__(self):
        self.cwd = os.getcwd()
        # Make sure at least one thing has been commited,
        # else it's not possible to create a new branch and
        # come back on master after that.
        if not self.repo.heads:
            logger.info("No branch exists, creating an empty commit to initialize the repository")
            # No branch
            if self.repo.is_dirty():
                self.repo.git.stash("push")
                self.repo.index.commit("First (empty) commit")
                self.repo.git.stash("pop")
            else:
                self.repo.index.commit("First (empty) commit")
        self.old_branch = self.repo.active_branch.name
        # Make sure the overleaf branch exists
        if not self.overleaf_branch in [b.name
                                        for b in self.repo.branches]:
            logger.info("Branch {} didn't exist, I will create it.".format(self.overleaf_branch))
            self.repo.git.checkout('-b', self.overleaf_branch)
        logger.debug("Currently on branch {}, I will stash push everything.".format(self.old_branch))
        ## `git stash push --all` does not push anything if nothing
        ## has been modified so we first check if there is
        ## anything to push:
        self.must_push_pop = self.repo.is_dirty(untracked_files=True)
        if self.must_push_pop:
            logger.debug("I will run: git stash push --all")
            self.repo.git.stash("push", "--all")
        else:
            logger.debug("Nothing to stash.")
        logger.debug("I'll go to branch {}".format(self.overleaf_branch))
        self.repo.heads[self.overleaf_branch].checkout()
        os.chdir(self.repo.working_tree_dir)
        return {'repo': self.repo,
                'old_branch': self.old_branch}

    def __exit__(self, type, value, traceback):
        logger.debug("Let's go back to branch {}".format(self.old_branch))
        self.repo.heads[self.old_branch].checkout()
        if self.must_push_pop:
            logger.debug("Let's run: git stash pop")
            self.repo.git.stash("pop")
        logger.debug("Let's go now to {}".format(self.cwd))
        os.chdir(self.cwd)

class cd:
    """Context manager for changing the current working directory.
    https://stackoverflow.com/questions/431684/how-do-i-change-directory-cd-in-python
    Usage: with cd("~/Library"):
    """
    def __init__(self, newPath):
        self.newPath = os.path.expanduser(newPath)

    def __enter__(self):
        self.savedPath = os.getcwd()
        os.chdir(self.newPath)

    def __exit__(self, etype, value, traceback):
        os.chdir(self.savedPath)

def ogit_ofetch(confproject=None, args=None):
    """
    Will simulate a kind of fetch on the overleaf branch, and
    basically sync this branch with the online overleaf version.
    """
    if not confproject:
        confproject = ConfProject(args=args)
    with OverleafRepo(confproject=confproject) as repo_dict:
        repo = repo_dict['repo']
        # Find a good destination folder for files
        d = datetime.now()
        base_name_hour = d.strftime("%Y_%m_%d_-_%H_%M_%S")
        base_name = base_name_hour
        current_svg_folder = os.path.join(confproject.get_svg_path(),
                                          base_name)
        i = 0
        while os.path.exists(current_svg_folder):
            i += 1
            base_name = "{}_-_{}".format(base_name_hour, i)
            current_svg_folder = os.path.join(confproject.get_svg_path(),
                                              base_name)
        should_keep_root_svg = os.path.exists(confproject.get_svg_path())
        file_zip = os.path.join(current_svg_folder,
                                base_name + ".zip")
        os.makedirs(current_svg_folder, exist_ok=True)
        overleaf = confproject.get_overleaf()
        overleaf.get_zip(outputfile=file_zip)
        # Extract the zip file
        extract_dir = os.path.join(current_svg_folder, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(file_zip,"r") as zip_ref:
            zip_ref.extractall(extract_dir)
        # Remove all the files created by git:
        repo.git.rm("-r", "-f", "--ignore-unmatch", ".")
        # Copy all files
        copy_tree(extract_dir, repo.working_tree_dir)
        # Add all these files
        os.chdir(extract_dir)
        files_to_add = []
        for f in Path(".").glob('**/*'):
            if f.is_file():
                files_to_add.append(str(f))
        os.chdir(repo.working_tree_dir)
        repo.index.add(files_to_add)
        # Commit
        if not repo.is_dirty():
            logger.debug("No change, nothing to commit.")
        else:
            repo.index.commit("New version from overleaf on {}".format(d.strftime("%a. %d %B %Y, %H:%M")))
        # If no svg is asked, remove the folder
        if not confproject.have_svg():
            if should_keep_root_svg:
                shutil.rmtree(current_svg_folder)
            else:
                shutil.rmtree(confproject.get_svg_path())
        else:
            # Remove only the extracted folder
            shutil.rmtree(extract_dir)

def ogit_opull(confproject=None, other_arguments=[], args=None):
    """
    This function will first fetch/sync the overleaf project into
    the branch, and then will merge the overleaf branch with the
    current branch.
    """
    if not confproject:
        confproject = ConfProject(args=args)
    ogit_ofetch(confproject)
    return run_interactive_command(["git", "merge", confproject.get_overleaf_branch_name()] + other_arguments)

def ogit_opush_force(confproject=None, should_merge_back=True, args=None):
    """Force to push everything online without pulling first"""
    if not confproject:
        confproject = ConfProject(args=args)
    logger.info("Let's push the files online...")
    repo = get_repo()
    overleaf = confproject.get_overleaf()
    with cd(repo.working_tree_dir):
        files_to_send = [ filename
                          for filename in repo.git.ls_files("-z").split('\x00')
                          if filename ]
        ### First send files
        for filename in files_to_send:
            logger.info("Will send file {}".format(filename))
            overleaf.upload_file(filename,
                                 local_path_name=filename,
                                 force=True,
                                 force_reload=confproject.get_force_reload())
        ### Then remove unused files
        ft = overleaf.ls(
            force_reload=confproject.get_force_reload()
        )
        online_files = [f.strip("/")
                        for f in ft.get_list_files()
                        if f]
        logger.debug("files_to_send: {}".format(files_to_send))
        logger.debug("online_files: {}".format(online_files))
        for filename in online_files:
            if not filename in files_to_send:
                logger.info("Will remove file {}".format(filename))
                overleaf.rm("/" + filename,
                            force=True,
                            force_reload=confproject.get_force_reload())
        ### Then remove unused folders
        # Make sure to remove '/' else you break completely the project!
        online_folders = [ f.strip("/")
                           for f in ft.get_list_folders()
                           if f.strip("/") ]
        logger.debug("online_folders: {}".format(online_folders))
        for folder in online_folders:
            # Check if the folder appears in the sent files
            # ... if not, remove it!
            if not [ f
                     for f in files_to_send
                     if f.startswith(folder + "/") ]:
                logger.info("Will remove folder {}".format(folder))
                overleaf.rm("/" + folder,
                           force=True,
                           force_reload=confproject.get_force_reload())
        logger.info("Push successful")
        if not should_merge_back:
            return 0
        logger.info("Let's merge back to overleaf branch!")
        ### Merge everything back to the overleaf branch
        with OverleafRepo(confproject=confproject) as repo_dict:
            repo = repo_dict['repo']
            old_branch = repo_dict['old_branch']
            return run_interactive_command(["git", "merge", old_branch])


def ogit_opush(confproject=None, allow_dirty_repo=False, other_arguments=[], args=None):
    """In order to avoid to get lose of information during push,
    we force the user to first do a pull."""
    if not confproject:
        confproject = ConfProject(args=args)
    repo = get_repo()
    if repo.is_dirty() and not allow_dirty_repo:
        txt = "This repository is dirty, please commit or stash before pushing."
        logger.error(txt)
        raise DirtyRepository(txt)
    logger.debug("Let's first pull before pushing notification")
    res_code = ogit_opull(confproject,
                          other_arguments=other_arguments)
    if res_code != 0:
        logger.error("An error occured during the merge, so we won't push anything.")
        raise ErrorDuringMerge()
    return ogit_opush_force(confproject=confproject)

def ogit_oremote_add(confproject=None, do_nothing_if_exists=None, args=None):
    """
    """
    try:
        repo = git.Repo(".")
        logger.debug("We are in a repository")
        path_to_save = os.path.join(repo.working_tree_dir, ".ogit_confproject")
    except git.InvalidGitRepositoryError:
        logger.debug("We are not in a repository")
        path_to_save = ".ogit_confproject"
    if os.path.exists(path_to_save):
        if do_nothing_if_exists == True:
            return None
        elif do_nothing_if_exists == None:
            r = input("A file .ogit_confproject already exists, do you want to continue and lose all the existing configuration?[y/N]")
            if not r.lower in ["y", "yes"]:
                return None
    if not confproject:
        confproject = ConfProject(try_to_find_conf=False)
    confproject.save(path_to_save)
    logger.info("The configuration file has been saved info {}".format(path_to_save))
    return confproject

def ogit_oclone(confproject=None, args=None):
    """
    This function basically clones the overleaf project into a new
    git project. If you already have an existing git project,
    use instead the function ogit_add_overleaf_remote.
    More precisely, this function
    - creates a repo
    - creates a branch "overleaf"
    - copy the overleaf project on the branch
    - add/commit all the overleaf files in that branch
    - merge this branch into master
    This is more or less equivalent to a `git init` and then ogit_pull().
    """
    try:
        logger.info("Is it a repo?")
        git.Repo(".")
        raise GitRepoAlreadyExist("A repository already exists here, if you want to sync this existing repository with overleaf, please see ofetch and opull (opull = ofetch + merge into current branch).")
    except git.InvalidGitRepositoryError:
        git.Repo.init(".")
    confproject = ogit_oremote_add(confproject=confproject)
    ogit_opull(confproject)

def demo_git():
    # confproject = ConfProject()
    # ogit_ofetch(confproject)
    # ogit_opull(confproject)
    # ogit_opush(confproject)
    # ogit_opush_force(confproject, should_merge_back=False)
    # ogit_oclone()
    # ogit_opush()
    pass
#demo_git()


def usage():
    print("Usage: not yet written, see readme!")

##############################
### Command Line Interface
##############################

def main():
    parser = argparse.ArgumentParser(description='ogit: Free git bridge between overleaf v2 and git')
    subparsers = parser.add_subparsers(help='Possible commands:', dest='command')

    parser.add_argument("-v", choices=['INFO', 'DEBUG', 'SPAM'])

    # oclone
    parser_oclone = subparsers.add_parser('oclone', help='Simulate a clone for an overleaf project')
    parser_oclone.set_defaults(func=ogit_oclone)

    # oremote_add
    parser_oremote_add = subparsers.add_parser('oremote_add', help='Add/replace the overleaf project url and user configuration and save it.')
    parser_oremote_add.set_defaults(func=ogit_oremote_add)

    # opush
    parser_opush = subparsers.add_parser('opush', help="First run opull to merge the online content on the current branch, and then push the modifications online if no conflict occurs (and merge the current branch back to the overleaf's reserved branch)")
    parser_opush.set_defaults(func=ogit_opush)

    # opush_force
    parser_opush_force = subparsers.add_parser('opush_force', help='Like opush, but does not do the opull first.')
    parser_opush_force.set_defaults(func=ogit_opush_force)

    # opull
    parser_opull = subparsers.add_parser('opull', help="Download the content from overleaf, put it on the overleaf's reserved_branch, and merge this branch with the current branch.")
    parser_opull.set_defaults(func=ogit_opull)

    # ofetch
    parser_ofetch = subparsers.add_parser('ofetch', help="Download the content from overleaf, and put in on the overleaf's reserved branch. If you want to merge, see opull")
    parser_ofetch.set_defaults(func=ogit_ofetch)

    # # XXX
    # parser_XXX = subparsers.add_parser('XXX', help='YYY')
    # parser_XXX.set_defaults(func=ogit_XXX)

    # Help sub-command
    parser_help = subparsers.add_parser('help', help='help me!')
    parser_help.set_defaults(func=lambda args: parser.print_help())

    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args=args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
