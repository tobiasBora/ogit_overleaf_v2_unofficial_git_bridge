# Ogit: overleaf v2 unofficial git bridge

WARNING: This repo has not yet be tested a lot. It comes without any warantee, and you could lose with it all your project, or even block it. To avoid such disaster, first test the project on dummy projects, do backups, and be aware that ogit always try to backup the project at every command you do in a folder `.ogit_svg`.

The goal of this project is to emulate the git bridge of the paid plan of overleaf v2 for free. It handle synchronization in both directions between a local git repo and an online overleaf v2 project. You should also be able to sync your repo with an external git repository, but you should manually do it for now with git. In the futur, it may be possible to sync with overleaf and with an external git repository in one command.

## Installation

Install `git`, `python3` and pip, and then install the required dependencies:

     $ pip install bs4 curlify websocket-client gitpython

Then, download this project where you want:

```
$ mkdir ogit
$ cd ogit
$ git clone https://github.com/tobiasBora/ogit_overleaf_v2_unofficial_git_bridge
$ cd ogit_overleaf_v2_unofficial_git_bridge/
```

Now, you need to add `ogit.py` to your `PATH` to make sure you can access the program from another git repository. You can do it for the current session with:

```
export PATH=$(pwd):${PATH}
``` 

or put this line in your `.bashrc` by replacing `$(pwd)` with the absolute directory of ogit.

Now, make sure to leave this git repository (git does not like to clone u git repository into an existing git repository):

```
cd ..
```

## Usage

To clone an existing overleaf project, first create a folder where the project will be:

```
$ mkdir myproject
$ cd myproject
```

and then run the following command:

```
$ ogit.py oclone
```

(Note the 'o' in front of clone that is added before most usual git commands)

This should ask you the url of the overleaf repo, your email address, and your password. Note that these credential will be saved into a file `.ogit_confproject`. You should be able to remove your password from this file if you don't want to have it written in plaintext on your hard drive. If you prefer to encrypt your password, you should be able to write a script that decrypt your password, and put it in the environment variable `OVERLEAF_PASSWORD` (see file `set_environment_MODEL.sh` for more details).

The script will create for you a backup folder `.ogit_svg` with a zip file of the project added at every new command, and a git repository with two branches, `overleaf` and `master`.

**NEVER** change the branch `overleaf` directly, unless you know what you do. This branch is maintained automatically by the script as a copy of the online overleaf repository.

To pull changes from overleaf on your local repo, run:

```
$ ogit.py opull
```

This command will synchronize the overleaf project with the `overleaf` branch, and merge this branch with the branch you are currently on (like master). In case of a conflict, just fix the conflict with the usual git commands.

When you have some changes to push online, do your commits (say on the `master` branch) :

```
$ git commit -am "Fix typos"
```

And then run:

```
$ ogit.py opush
```

This command will basically run a `ogit.py opull` to sync overleaf with the `overleaf` branch and merge `overleaf` branch with your current branch. If no conflict occurs, it then copies your current branch online, and finally merge back your current branch with the branch `overleaf` to make sure both branches are equal.

More commands are available, run just:

```
$ ogit.py 
```

to get the list.

Enjoy, and feel free to ask question, report bugs, or contribute in the github bug report/PR sections! 
