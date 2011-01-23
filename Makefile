all:
	rm -rf ankimini
	git clone /usr/src/my-stuff/anki/ankimini
	rm -rf ankimini/.git
	git clone /usr/src/my-stuff/anki/libanki ankimini/libanki
	rm -rf ankimini/libanki/.git
	cp -a /usr/share/pyshared/simplejson ankimini/
	cp -a /usr/share/pyshared/sqlalchemy ankimini/
	mkdir ankimini/decks
	cp README.windows-mobile.txt ankimini
	-find ankimini -iname "*.pyc"|xargs rm -v
	zip -9 -r `date "+%F"`-ankimini-`./get_prog_version`-windows-mobile-libanki-`./get_lib_version`.zip ankimini	
