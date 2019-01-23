#!/usr/bin/env python3

from bs4 import BeautifulSoup
import json
import requests
import curlify
from getpass import getpass
import os
from websocket import create_connection # websocket-client
import logging
import zipfile
import ntpath

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
logger.setLevel(logging.INFO)
# logger.setLevel(logging.DEBUG)
# logger.setLevel(logging.SPAM)

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
    
class ImpossibleError(OverleafException):
    """This class of error are raised when an error should not occur. For example mkdir with force=True should create a folder..."""
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
        Parent_id should be None for folders."""
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
    def __init__(self, url_project, email=None, password=None):
        self.email = email or os.environ.get("OVERLEAF_EMAIL") or input("email? ")
        self.password = os.environ.get("OVERLEAF_PASSWORD") or getpass("password? ")
        self.old_overleaf_session = None
        self.overleaf_session = None
        self.csrf_token = None
        self.file_tree = None
        if url_project[-1] != "/":
            self.url_project = url_project + "/"
        else:
            self.url_project = url_project
        self._connect() # sets old_overleaf_session and csrf_token

    def _connect(self):
        # Go to the login page
        logger.info('#### Trying to connect...')
        try:
            logger.debug('## 1) Go to login page')
            r = requests.get('https://www.overleaf.com/login')
            soup = BeautifulSoup(r.text, "html.parser")
            self.csrf_token = soup.find('input', {'name':'_csrf'})['value']
            self.old_overleaf_session = r.cookies["overleaf_session"]

            logger.debug('The old overleaf session is {}'.format(self.old_overleaf_session))
            logger.debug('The csrf token is {}'.format(self.csrf_token))
            # Send the login informations
            logger.debug('## 2) Send the email/passwd informations')
            r = requests.post('https://www.overleaf.com/login',
                      cookies = {'overleaf_session': self.old_overleaf_session},
                      headers = {'Content-Type': 'application/json;charset=UTF-8',
                                 'Accept': 'application/json, text/plain, */*'},
                      json = {'_csrf': self.csrf_token,
                              'email': self.email,
                              'password': self.password})
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
            logger.info("The good overleaf session is {}".format(self.overleaf_session))
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
            r = requests.get('{}download/zip'.format(self.url_project),
                             cookies = {'overleaf_session': self.overleaf_session})
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
            logger.debug("The file tree already exists, and we don't force to reload, so I'll provide the same file_tree as before: {}".format(self.file_tree))
            return self.file_tree
        logger.info("#### Getting list of files and folders of the project {}".format(self.url_project))
        try:
            r = requests.get('https://www.overleaf.com/socket.io/1/',
                             cookies = {'overleaf_session': self.overleaf_session,
                                        'SERVERID': 'sl-lin-prod-web-5'}
            )
            logger.debug("request to get io: {}".format(r.text))
            socket_url = r.text.split(':')[0]
            full_wsurl = "wss://www.overleaf.com/socket.io/1/websocket/{}".format(socket_url)
            logger.debug("full_wsurl: {}".format(full_wsurl))
            ws = create_connection(full_wsurl,
                                   cookie = "SERVERID=sl-lin-prod-web-5; overleaf_session={};".format(self.overleaf_session))
            out_json = None
            while True:
                logger.debug("Waiting to receive a message...")
                resp = ws.recv()
                logger.debug("I received: {}".format(resp))
                if resp[0] =='5':
                    to_send = """5:1+::{"name":"joinProject","args":[{"project_id":"5c3317b393083f2e21158498"}]}"""
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
                               parent_id = None)
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
        r = requests.delete("{}{}{}".format(self.url_project,
                                            mid_url,
                                            _id),
                            cookies = {'overleaf_session': self.overleaf_session},
                            headers = {'Accept': 'application/json, text/plain, */*',
                                       'X-Csrf-Token': self.csrf_token})
        logger.debug(r.text)


    def mkdir(self, online_path, force=False, force_reload=True, nb_retry=1):
        """Create (recursively if needed) a folder online"""
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
                r = requests.post('https://www.overleaf.com/project/5c3317b393083f2e21158498/folder',
                                  cookies = {'overleaf_session': self.overleaf_session},
                                  headers = {'Content-Type': 'application/json;charset=UTF-8',
                                             'Accept': 'application/json, text/plain, */*'},
                                  json = {'_csrf': self.csrf_token,
                                          'parent_folder_id': parent_id,
                                          'name': p})
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

    def mv(self, src, dst_folder, new_name=None, create_folder=False, create_folder_even_if_erase=False, force=False, force_reload=True, nb_retry=1):
        """Move src to dst_folder, and eventually change
        the name to new_name. It can move both files and folders.
        If force=True, do not raise an error even if the input file does not exist."""
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
        dst_elt = ft.get_element(dst_folder)
        if not dst_elt:
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
            logger.warning('The dst {} you are trying to move to is a file, not a folder!'.format(dst_folder))
            if not create_folder_even_if_erase:
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
        logger.debug("I will move the file {} to the folder {}".format(src, dst_folder))
        url = '{}{}{}/move'.format(self.url_project,
                                   mid_url,
                                   src_elt['_id'])
        r = requests.post(url,
                          cookies = {'overleaf_session': self.overleaf_session},
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
            self.file_tree.add_element(name=src_elt['name'],
                                       path=dst_folder,
                                       _id=src_elt['_id'],
                                       file_type=src_elt['file_type'],
                                       parent_id=dst_elt['_id'])
            self.file_tree.remove_element(src)
        # Rename file if needed
        if new_name:
            url = '{}{}{}/rename'.format(self.url_project,
                                         mid_url,
                                         src_elt['_id'])
            r = requests.post(url,
                              cookies = {'overleaf_session': self.overleaf_session},
                              headers = {'Content-Type': 'application/json;charset=UTF-8',
                                         'Accept': 'application/json, text/plain, */*'},
                              json = {'name': new_name,
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
                self.file_tree.add_element(name=new_name,
                                           path=dst_folder,
                                           _id=src_elt['_id'],
                                           file_type=src_elt['file_type'],
                                           parent_id=dst_elt['_id'])
                dst_folder_canon = self.file_tree.get_canon_path(dst_folder, should_finish_slash=True)
                self.file_tree.remove_element(dst_folder_canon + src_elt['name'])
            
    def upload_file(self, online_path_name, local_path_name, force_reload=True):
        self.ls(force_reload=force_reload)
        if online_path_name[0] != "/":
            online_path_name = "/" + online_path_name
        online_path, online_filename = ntpath.split(online_path_name)
        if not filename:
            raise ErrorUploadFile("The path {} does not have a valid filename.".format(online_path_name))
        path_id = self.file_tree.get_element(online_path)
        # if not path_id:
            # TODO: create folder
        r = requests.post("{}upload?folder_id={}&_csrf={}".format(self.url_project, path_id, csrf_token),
                          cookies = {'overleaf_session': overleaf_session},
                          # data=payload,
                  files = {'qqfile': ('othermain.tex', open('/tmp/d/main.tex', 'rb'))})

# Overleaf('https://www.overleaf.com/project/5c3317b393083f2e21158498/').get_zip()
# Overleaf('https://www.overleaf.com/project/5c3317b393083f2e21158498/').ls()
# Overleaf('https://www.overleaf.com/project/5c3317b393083f2e21158498/').mkdir("/ogit/script/")
# Overleaf('https://www.overleaf.com/project/5c3317b393083f2e21158498/').mkdir("/aa.txt")
# Overleaf('https://www.overleaf.com/project/5c3317b393083f2e21158498/').rm("/bb.txt")
# Overleaf('https://www.overleaf.com/project/5c3317b393083f2e21158498/').mv("/name.tex", "/myfolder3/script/")
# Overleaf('https://www.overleaf.com/project/5c3317b393083f2e21158498/').mv("/myfolder3/script/", "/")
o = Overleaf('https://www.overleaf.com/project/5c3317b393083f2e21158498/')
print(o.ls())
print("Let's start to play :D")
#o.mv("/name.tex", "/myfolder3/script/", force_reload=False)
#o.mv("/myfolder3/script/name.tex", "/", force_reload=False)
#o.mv("/myfolder3/script/cren.zip", "/", force_reload=False)
#o.mv("/cren.zip", "/myfolder3/script/", new_name="cren_rename_ogit.zip", force_reload=False)
#o.mv("/name.tex", "/myfolder3/script/", new_name="name_ren.tex", force_reload=False)

