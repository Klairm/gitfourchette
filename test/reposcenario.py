import shutil

from .util import *
from gitfourchette.porcelain import *


def fileWithStagedAndUnstagedChanges(path):
    with RepoContext(path) as repo:
        writeFile(F"{path}/a/a1.txt", "a1\nstaged change\n")
        repo.index.read()
        repo.index.add("a/a1.txt")
        repo.index.write()
        writeFile(F"{path}/a/a1.txt", "a1\nUNSTAGED CHANGE TO REVERT\nstaged change\n")
        assert repo.status() == {"a/a1.txt": FileStatus.INDEX_MODIFIED | FileStatus.WT_MODIFIED}


def stagedNewEmptyFile(path):
    with RepoContext(path) as repo:
        writeFile(F"{path}/SomeNewFile.txt", "")
        repo.index.read()
        repo.index.add("SomeNewFile.txt")
        repo.index.write()
        assert repo.status() == {"SomeNewFile.txt": FileStatus.INDEX_NEW}


def stashedChange(path):
    with RepoContext(path) as repo:
        writeFile(F"{path}/a/a1.txt", "a1\nPENDING CHANGE\n")
        repo.stash(TEST_SIGNATURE, "helloworld")
        assert repo.status() == {}


def submodule(path, absorb=False):
    subPath = os.path.join(path, "submo")
    shutil.copytree(path, subPath)

    # Make bare copy of submodule so that we can use it as a remote and test UpdateSubmodule
    makeBareCopy(subPath, "submo-localfs", preFetch=True, barePath=f"{path}/../submodule-bare-copy.git")

    with RepoContext(subPath) as subRepo:
        subRepo.remotes.delete("origin")  # nuke origin remote to prevent net access in UpdateSubmodule
        subRepo.branches.local["master"].upstream = subRepo.branches.remote["submo-localfs/master"]
        subRemoteUrl = subRepo.remotes["submo-localfs"].url

    with RepoContext(path, write_index=True) as repo:
        repo.add_inner_repo_as_submodule("submo", subRemoteUrl, absorb_git_dir=absorb)
        subAddCommit = repo.create_commit_on_head("Add Submodule for Test Purposes")

    return subPath, subAddCommit
