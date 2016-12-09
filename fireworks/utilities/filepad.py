# coding: utf-8

from __future__ import division, print_function, unicode_literals, absolute_import

"""
This module defines the core classes
"""
import zlib
import os

from pymongo import MongoClient
import gridfs

from monty.serialization import loadfn
from monty.json import MSONable

from fireworks.fw_config import LAUNCHPAD_LOC
from fireworks.utilities.fw_utilities import get_fw_logger


__author__ = 'Kiran Mathew'
__email__ = 'kmathew@lbl.gov'


class FilePad(MSONable):

    def __init__(self, host='localhost', port=27017, database='fireworks', username=None,
                 password=None, filepad="filepad", gridfs_collection="fpad_gfs", logdir=None,
                 strm_lvl=None):
        self.host = host
        self.port = int(port)
        self.database = database
        self.username = username
        self.password = password
        try:
            self.connection = MongoClient(self.host, self.port)
            self.db = self.connection[database]
        except:
            raise Exception("connection failed")
        try:
            if self.username:
                self.db.authenticate(self.username, self.password)
        except:
            raise ValueError("authentication failed")

        # set collections: filepad and gridfs
        self.filepad = self.db[filepad]
        self.gridfs = gridfs.GridFS(self.db, gridfs_collection)

        # logging
        self.logdir = logdir
        self.strm_lvl = strm_lvl if strm_lvl else 'INFO'
        self.logger = get_fw_logger('filepad', l_dir=self.logdir, stream_level=self.strm_lvl)

    def add_file(self, path, label=None, compress=True, metadata=None, additional_data=None):
        """
        Insert the file specified by the path into gridfs and the id and label(if provided) returned.
        Note: No insertion if the label already exists in the db.

        Args:
            path (str): path to the file
            label (str): file label
            compress (bool): compress or not
            metadata (dict): file metadata
            additional_data (dict): dict of additional stuff to add to the file document
                timestamp, metadata, creator, etc

        Returns:
            (str, str): the id returned by gridfs, label
        """
        # skip if the label exists
        if label is not None:
            f = self.get_file(label)
            if f is not None:
                self.logger.warning("label: {} exists. Skipping insertion".format(label))
                return f[1]["file_id"], f[1]["label"]
        metadata = metadata or {}
        path = os.path.abspath(path)
        metadata.update({"path": path})
        additional_data = additional_data or {}
        additional_data.update({"original_file_name": os.path.basename(path)})
        with open(path, "r") as f:
            contents = f.read()
            return self.insert_contents(contents, label=label, compress=compress, metadata=metadata,
                                        additional_data=additional_data)

    def get_file(self, label):
        """
        get file by label

        Args:
            label (str): the file label

        Returns:
            (str, dict): the file content as a string, document dictionary
        """
        doc = self.filepad.find_one({"label": label})
        return self.get_file_by_id(doc["file_id"]) if doc else None

    def delete_file(self, label):
        """
        Delete all documents with matching label

        Args:
            label (str): the file label
        """
        docs = self.filepad.find({"label": label})
        for d in docs:
            self.delete_file_by_id(d["file_id"])
        self.filepad.delete_many({"label": label})

    def update_file(self, label, path, delete_old=False):
        """
        Update the file in the gridfs and retain the rest.

        Args:
            file_id (str): the file id

        Returns:
            (str, str): old file id , new file id
        """
        doc = self.filepad.find({"label": label})[-1]
        return self.update_file_by_id(doc["file_id"], path, delete_old=delete_old)

    def insert_contents(self, contents, label=None, compress=True, metadata=None, additional_data=None):
        """

        Args:
            contents (str): file contents or any arbitrary string to be stored in gridfs
            label (str): file label
            compress (bool): compress or not
            metadata (dict): file metadata
            additional_data (dict): dict of additional stuff to add to the file document

        Returns:
            (str, str): the id returned by gridfs, label
        """
        file_id = self._insert_to_gridfs(contents, compress=compress)
        metadata = metadata or {}
        additional_data = additional_data or {}
        d = {"label": label, "metadata": metadata}
        d.update(additional_data)
        d["file_id"] = file_id
        self.filepad.insert_one(d)
        return file_id, label

    def _insert_to_gridfs(self, contents, compress=True):
        if compress:
            contents = zlib.compress(contents.encode(), compress)
        # insert to gridfs
        return str(self.gridfs.put(contents))

    def get_file_by_id(self, file_id):
        """

        Args:
            file_id (str): the file id

        Returns:
            (str, dict): the file content as a string, document dictionary
        """
        from bson.objectid import ObjectId

        doc = self.filepad.find_one({"file_id": file_id})
        if doc:
            gfs_id = doc['file_id']
            file_contents = zlib.decompress(self.gridfs.get(ObjectId(gfs_id)).read())
            return file_contents, doc
        else:
            return None, None

    def get_file_by_query(self, query):
        """

        Args:
            query (dict): pymongo query dict

        Returns:
            list: list of all (file content as a string, document dictionary)
        """
        all_files = []
        for d in self.filepad.find(query):
            all_files.append(self.get_file_by_id(d["file_id"]))
        return all_files

    def delete_file_by_id(self, file_id):
        """

        Args:
            file_id (str): the file id
        """
        self.gridfs.delete(file_id)
        self.filepad.delete_one({"file_id": file_id})

    def delete_file_by_query(self, query):
        """

        Args:
            query (dict): pymongo query dict
        """
        for d in self.filepad.find(query):
            self.delete_file_by_id(d["file_id"])

    def update_file_by_id(self, file_id, path, delete_old=False):
        """
        Update the file in the gridfs with the given id and retain the rest of the document.

        Args:
            file_id (str): the file id

        Returns:
            (str, str): old file id , new file id
        """
        doc = self.filepad.find_one({"file_id": file_id})
        old_file_id = doc["file_id"]
        if delete_old:
            self.gridfs.delete(old_file_id)
        file_id = self._insert_to_gridfs(open(path, "r").read())
        doc["file_id"] = file_id
        return old_file_id, file_id

    @classmethod
    def from_db_file(cls, db_file, admin=True):
        """

        Args:
            db_file (str): path to the filepad cred file

        Returns:
            FilePad object
        """
        creds = loadfn(db_file)

        if admin:
            user = creds.get("admin_user")
            password = creds.get("admin_password")
        else:
            user = creds.get("readonly_user")
            password = creds.get("readonly_password")

        return cls(creds.get("host", "localhost"), int(creds.get("port", 27017)),
                   creds.get("database", "fireworks"), user, password, creds.get("filepad", "filepad"),
                   creds.get("gridfs_collection", "fpad_gfs"))

    @classmethod
    def auto_load(cls):
        """
        Returns FilePad object
        """
        if LAUNCHPAD_LOC:
            return FilePad.from_db_file(LAUNCHPAD_LOC)
        return FilePad()
