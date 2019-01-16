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
# logger.setLevel(logging.INFO)
# logger.setLevel(logging.DEBUG)
logger.setLevel(logging.SPAM)

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

##############################
### Way to represent a file/folder in overleaf
##############################

class FileTree:
    """Represents a file/folder tree on overleaf's website.
    We register here the file/folder ids, as well as the
    """
    def __init__(self):
        self.l = dict()

    def add_element(self, name, path, _id, is_file, parent_id=None):
        """a path starts with a slash and ends with a slash.
        Parent_id should be None for folders."""
        # Path should start and end with /
        if len(path) == 0:
            path = "/"
        else:
            if path[0] != "/":
                path = "/" + path
            if path[-1] != "/":
                path = path + "/"
        # name should not contain '/'
        name = name.replace("/", "")
        self.l[path + name] = {
            'name': name,
            'path': path,
            '_id': _id,
            'is_file': is_file,
            'parent_id': parent_id}

    def get_element(self, path_name):
        """Get the element corresponding to the given path name.
        In case the element does not exist, return None."""
        if len(path_name) == 0:
            path_name = "/"
        else:
            # No / at the end of folders
            if path_name[-1] == "/":
                path_name = path_name[:-1]
            # path starts with /
            if path_name[0] != "/":
                path_name = "/" + path_name
        try:
            return self.l[path_name]
        except KeyError:
            return None

    def __str__(self):
        s = ""
        for path_name, d in self.l.items():
            s += path_name + (" (File)" if d['is_file'] else " (Dir)") + "\n"
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

    def get_list_files_and_folders(self):
        """This function gets the list of files and folders on the
        online overleaf website, and sets self.file_tree to the associated
        file tree.
        """
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
                               is_file = False,
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
                for doc in json_folders['docs'] + json_folders['fileRefs']:
                    ft.add_element(name = doc['name'],
                                   path = path,
                                   _id = doc['_id'],
                                   is_file = True,
                                   parent_id = current_folder_id)
            iterate_folder(rootFolderJson)
            self.file_tree = ft
            logger.debug(ft)
            return ft
        except Exception as e:
            raise BadFormatJsonListFilesFolders(e) from e

    def create_folder(self, online_path):
        """Create (recursively if needed) a folder online"""
        subfolders = [p for p in online_path.split() if p]
        for p in subfolders:
            TODO
        
    def upload_file(self, online_path_name, local_path_name, force_reload=False):
        if not self.file_tree or force_reload:
            self.get_list_files_and_folders()
        if online_path_name[0] != "/":
            online_path_name = "/" + online_path_name
        online_path, online_filename = ntpath.split(online_path_name)
        if not filename:
            raise ErrorUploadFile("The path {} does not have a valid filename.".format(online_path_name))
        path_id = self.file_tree.get_element(online_path)
        if not path_id:
            # TODO: create folder
        r = requests.post("{}upload?folder_id={}&_csrf={}".format(self.url_project, path_id, csrf_token),
                          cookies = {'overleaf_session': overleaf_session},
                          # data=payload,
                  files = {'qqfile': ('othermain.tex', open('/tmp/d/main.tex', 'rb'))})

        
Overleaf('https://www.overleaf.com/project/5c3317b393083f2e21158498/').get_list_files_and_folders()
