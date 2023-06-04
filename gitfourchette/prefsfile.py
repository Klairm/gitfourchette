import dataclasses
import enum
import json

from gitfourchette import log
from gitfourchette.qt import *


class PrefsJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, bytes):
            return {"_type": "bytes", "data": obj.hex()}
        return super(self).default(obj)


class PrefsJSONDecoder(json.JSONDecoder):
    def __init__(self, *args, **kwargs):
        json.JSONDecoder.__init__(self, object_hook=self.object_hook, *args, **kwargs)

    def object_hook(self, j):
        if '_type' not in j:
            return j
        type = j['_type']
        if type == "bytes":
            return bytes.fromhex(j["data"])
        return j


class PrefsFile:
    def getParentDir(self):
        return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)

    def _getFullPath(self, forWriting: bool):
        prefsDir = self.getParentDir()
        if not prefsDir:
            return None

        if forWriting:
            os.makedirs(prefsDir, exist_ok=True)

        fullPath = os.path.join(prefsDir, getattr(self, 'filename'))

        if not forWriting and not os.path.isfile(fullPath):
            return None

        return fullPath

    def write(self, force=False):
        from gitfourchette.settings import TEST_MODE
        if not force and TEST_MODE:
            log.info("prefs", "Disabling write prefs")
            return None

        prefsPath = self._getFullPath(forWriting=True)

        if not prefsPath:
            log.warning("prefs", "Couldn't get path for writing")
            return None

        # Get default values if we're saving a dataclass
        defaults = {}
        if dataclasses.is_dataclass(self):
            for f in dataclasses.fields(self):
                if f.default_factory != dataclasses.MISSING:
                    defaults[f.name] = f.default_factory()
                else:
                    defaults[f.name] = f.default

        # Skip private fields starting with an underscore,
        # and skip fields that are set to the default value
        filtered = {}
        for k in self.__dict__:
            if k.startswith("_"):
                continue
            v = self.__dict__[k]
            if (k not in defaults) or (defaults[k] != v):
                filtered[k] = v

        # Dump the object to disk
        with open(prefsPath, 'wt', encoding='utf-8') as jsonFile:
            json.dump(obj=filtered, fp=jsonFile, indent='\t', cls=PrefsJSONEncoder)

        log.info("prefs", f"Wrote {prefsPath}")
        return prefsPath

    def load(self):
        prefsPath = self._getFullPath(forWriting=False)
        if not prefsPath:  # couldn't be found
            return False

        with open(prefsPath, 'rt', encoding='utf-8') as f:
            try:
                obj = json.load(f, cls=PrefsJSONDecoder)
            except ValueError as loadError:
                log.warning("prefs", F"{prefsPath}: {loadError}")
                return False

            for k in obj:
                if k.startswith('_'):
                    log.warning("prefs", F"{prefsPath}: skipping illegal key: {k}")
                    continue
                if k not in self.__dict__:
                    log.warning("prefs", F"{prefsPath}: skipping unknown key: {k}")
                    continue

                originalType = type(self.__dict__[k])
                if issubclass(originalType, enum.IntEnum):
                    acceptedType = int
                else:
                    acceptedType = originalType

                if type(obj[k]) != acceptedType:
                    log.warning("prefs", F"{prefsPath}: value type mismatch for {k}: expected {acceptedType}, got {type(obj[k])}")
                    continue
                self.__dict__[k] = originalType(obj[k])

        return True
