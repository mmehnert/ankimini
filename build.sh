#!/bin/sh

rm -rf ankimini

git clone /usr/src/my-stuff/anki/ankimini
PROG_VERSION=`./get_prog_version`
rm -rf ankimini/.git

git clone /usr/src/my-stuff/anki/libanki ankimini/libanki
LIB_VERSION=`./get_lib_version`
rm -rf ankimini/libanki/.git

cp -a /usr/share/pyshared/simplejson ankimini/
cp -a /usr/share/pyshared/sqlalchemy ankimini/

mkdir ankimini/decks

cp README.windows-mobile.txt ankimini

-find ankimini -iname "*.pyc"|xargs rm -v

zip -9 -x \*.gitignore -r `date "+%F"`-ankimini-$PROG_VERSION-windows-mobile-libanki-$LIB_VERSION.zip ankimini	
