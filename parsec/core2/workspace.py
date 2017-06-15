import arrow
import json

from parsec.exceptions import InvalidPath
from parsec.tools import to_jsonb64, from_jsonb64


class UserManifest:
    def __init__(self):
        pass


class FileManifest:
    def __init__(self):
        pass


class FileBlock:
    def __init__(self):
        pass


class File:
    def __init__(self, created=None, updated=None, data=None):
        self.data = data or b''
        now = arrow.get()
        self.created = created or now
        self.updated = updated or now

    def dump(self):
        return json.dumps({
            'data': to_jsonb64(self.data),
            'created': self.created.toisoformat(),
            'updated': self.updated.toisoformat()
        })

    @classmethod
    def load(cls, payload):
        jsonpayload = json.loads(payload)
        return cls(
            created=arrow.get(jsonpayload['created']),
            updated=arrow.get(jsonpayload['updated']),
            data=from_jsonb64(jsonpayload['data'])
        )


class Folder:
    def __init__(self, created=None, updated=None, children=None):
        self.children = children or {}
        now = arrow.get()
        self.created = created or now
        self.updated = updated or now

    # def dump(self):
    #     return json.dumps({
    #         'children': {k: v.dump() for k, v in },
    #         'created': self.created.toisoformat(),
    #         'updated': self.updated.toisoformat()
    #     })

    # @classmethod
    # def load(cls, payload):
    #     jsonpayload = json.loads(payload)
    #     return cls(
    #         created=arrow.get(jsonpayload['created']),
    #         updated=arrow.get(jsonpayload['updated']),
    #         data=from_jsonb64(jsonpayload['data'])
    #     )

class Workspace(Folder):
    pass
    # def dump(self):
    #     pass

    # def load(self):
    #     pass


def _retrieve_file(workspace, path):
    fileobj = _retrieve_path(workspace, path)
    if not isinstance(fileobj, File):
        raise InvalidPath("Path `%s` is not a file" % path)
    return fileobj


def _check_path(workspace, path, should_exists=True, type=None):
    if path == '/':
        if not should_exists or type not in ('folder', None):
            raise InvalidPath('Root `/` folder always exists')
        else:
            return
    dirpath, leafname = path.rsplit('/', 1)
    try:
        obj = _retrieve_path(workspace, dirpath)
        if not isinstance(obj, Folder):
            raise InvalidPath("Path `%s` is not a folder" % path)
        try:
            leafobj = obj.children[leafname]
            if not should_exists:
                raise InvalidPath("Path `%s` already exist" % path)
            if (type == 'file' and not isinstance(leafobj, File) or
                    type == 'folder' and not isinstance(leafobj, Folder)):
                raise InvalidPath("Path `%s` is not a %s" % (path, type))
        except KeyError:
            if should_exists:
                raise InvalidPath("Path `%s` doesn't exist" % path)
    except InvalidPath:
        raise InvalidPath("Path `%s` doesn't exist" % (path if should_exists else dirpath))


def _retrieve_path(workspace, path):
    if not path:
        return workspace
    if not path.startswith('/'):
        raise InvalidPath("Path must start with `/`")
    parent_dir = cur_dir = workspace
    reps = path.split('/')
    for rep in reps:
        if not rep or rep == '.':
            continue
        elif rep == '..':
            cur_dir = parent_dir
        else:
            try:
                parent_dir, cur_dir = cur_dir, cur_dir.children[rep]
            except KeyError:
                raise InvalidPath("Path `%s` doesn't exist" % path)
    return cur_dir



class Reader:
    def __init__(self, identity, backend):
        self._identity = identity
        self._backend = backend

    async def file_read(self, workspace: Workspace, path: str, offset: int=0, size: int=-1):
        self._identity.id
        _check_path(workspace, path, should_exists=True, type='file')
        fileobj = _retrieve_file(workspace, path)
        if size < 0:
            return fileobj.data[offset:]
        else:
            return fileobj.data[offset:offset + size]

    async def stat(self, workspace: Workspace, path: str):
        self._identity.id
        _check_path(workspace, path, should_exists=True)
        obj = _retrieve_path(workspace, path)
        if isinstance(obj, Folder):
            return {'created': obj.created, 'updated': obj.updated,
                    'type': 'folder', 'children': list(obj.children.keys())}
        else:
            return {'created': obj.created, 'updated': obj.updated,
                    'type': 'file', 'size': len(obj.data)}


class Writer:
    def __init__(self, identity, backend):
        self._identity = identity
        self._backend = backend

    async def file_create(self, workspace, path: str):
        self._identity.id
        _check_path(workspace, path, should_exists=False)
        dirpath, name = path.rsplit('/', 1)
        dirobj = _retrieve_path(workspace, dirpath)
        dirobj.children[name] = File()

    async def file_write(self, workspace, path: str, content: bytes, offset: int=0):
        self._identity.id
        _check_path(workspace, path, should_exists=True, type='file')
        fileobj = _retrieve_file(workspace, path)
        fileobj.data = (fileobj.data[:offset] + content +
                           fileobj.data[offset + len(content):])
        fileobj.updated = arrow.get()

    async def folder_create(self, workspace, path: str):
        self._identity.id
        _check_path(workspace, path, should_exists=False)
        dirpath, name = path.rsplit('/', 1)
        dirobj = _retrieve_path(workspace, dirpath)
        dirobj.children[name] = Folder()

    async def move(self, workspace, src: str, dst: str):
        self._identity.id
        _check_path(workspace, src, should_exists=True)
        _check_path(workspace, dst, should_exists=False)

        srcdirpath, scrfilename = src.rsplit('/', 1)
        dstdirpath, dstfilename = dst.rsplit('/', 1)

        srcobj = _retrieve_path(workspace, srcdirpath)
        dstobj = _retrieve_path(workspace, dstdirpath)
        dstobj.children[dstfilename] = srcobj.children[scrfilename]
        del srcobj.children[scrfilename]

    async def delete(self, workspace, path: str):
        self._identity.id
        _check_path(workspace, path, should_exists=True)
        dirpath, leafname = path.rsplit('/', 1)
        obj = _retrieve_path(workspace, dirpath)
        del obj.children[leafname]

    async def file_truncate(self, workspace, path: str, length: int):
        self._identity.id
        _check_path(workspace, path, should_exists=True, type='file')
        fileobj = _retrieve_file(workspace, path)
        fileobj.data = fileobj.data[:length]
        fileobj.updated = arrow.get()


class JournalizedWriter(Writer):
    def __init__(self):
        super().__init__()
        # TODO: journal should be persisted in disk
        self._journal = []

    async def file_create(self, workspace, path: str):
        self._journal.append(('file_create', path))
        return await super().file_create(workspace, path)

    async def file_write(self, workspace, path: str, content: bytes, offset: int=0):
        self._journal.append('file_write', path, content, offset)
        return await super().file_write(self, workspace, path, content, offset)

    async def folder_create(self, workspace, path: str):
        self._journal.append('folder_create', path)
        return await super().folder_create(self, workspace, path)

    async def move(self, workspace, src: str, dst: str):
        self._journal.append('move', src, dst)
        return await super().move(self, workspace, src, dst)

    async def delete(self, workspace, path: str):
        self._journal.append('delete', path)
        return await super().delete(self, workspace, path)

    async def file_truncate(self, workspace, path: str, length: int):
        self._journal.append('file_truncate', path, length)
        return await super().file_truncate(self, workspace, path, length)


def _load_file(entry):
    assert isinstance(entry, dict)
    return File(arrow.get(entry['created']), arrow.get(entry['updated']))


def _load_folder(entry, folder_cls=Folder):
    assert isinstance(entry, dict)
    assert isinstance(entry['children'], dict)
    children = {}
    for name, child in entry['children'].items():
        if child['type'] == 'file':
            children[name] = _load_file(child)
        else:
            children[name] = _load_folder(child)
    return folder_cls(arrow.get(entry['created']), arrow.get(entry['updated']), children)


def workspace_factory(user_manifest=None):
    if user_manifest is None:
        return Workspace()
    assert isinstance(user_manifest, dict)
    assert user_manifest['type'] == 'folder'
    return _load_folder(user_manifest, folder_cls=Workspace)


class Dumper:
    def _dump(self, item):
        if isinstance(item, File):
            return {
                'type': 'file',
                'updated': item.updated.isoformat(),
                'created': item.created.isoformat(),
            }
        elif isinstance(item, Folder):
            return {
                'type': 'folder',
                'updated': item.updated.isoformat(),
                'created': item.created.isoformat(),
                'children': {k: self._dump(v) for k, v in item.children.items()}
            }
        else:
            raise RuntimeError('Invalid node type %s' % item)

    def dump(self, workspace):
        assert isinstance(workspace, Workspace)
        return self._dump(workspace)
