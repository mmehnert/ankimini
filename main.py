# -*- coding: utf-8 -*-
# Copyright: Damien Elmes <anki@ichi2.net>
# License: GNU GPL, version 3 or later; http://www.gnu.org/copyleft/gpl.html
#

"""\
A mini anki webserver
======================
"""
__docformat__ = 'restructuredtext'
import sys,os
sys.path.insert(0,os.getcwd()+os.sep+"libanki")
sys.path.append(os.getcwd()+os.sep+"simplejson")
sys.path.append(os.getcwd()+os.sep+"sqlalchemy")

import time, cgi, sys, os, re, threading, traceback
from BaseHTTPServer import HTTPServer
from SimpleHTTPServer import SimpleHTTPRequestHandler
from anki import DeckStorage as ds
from anki.sync import SyncClient, HttpSyncServerProxy
from anki.media import mediaFiles
from anki.utils import parseTags, joinTags
from anki.facts import Fact
from anki.hooks import addHook

####### VERSIONS #########

from anki import version as VERSION_LIBANKI
VERSION_ANKIMINI="2.0"

##########################

ANKIMINI_PATH=os.getcwd()

class Config(dict):
    configFile = os.path.join(ANKIMINI_PATH,"ankimini-config.py")
    re_matchComments = re.compile('#.*')
    re_matchConfig = re.compile('\s*(\w+)\s*=([^#]*)')

    def __init__(self, filename=None ):
        dict.__init__(self)
        self.setDefaults()
        if filename:
          self.configFile = filename

    # setup some defaults in case problems reading config file
    def setDefaults(self):
        self['DEBUG_NETWORK']=False
        self['SERVER_PORT']=8000
        self['DECK_PATH']=os.path.join(ANKIMINI_PATH,"sample-deck.anki")
        self['SYNC_USERNAME']="changeme"
        self['SYNC_PASSWORD']="changeme"
        self['PLAY_COMMAND']="play"
        self['USE_LOCAL_CSS']=False
        self['LOCAL_CSS_FILE']='basic.css'
        self['DISPLAY_INTERVAL']=True
        self['DISPLAY_DIVIDER']=True

    def loadConfig(self):
        try:
            import re
            for line in open(self.configFile,"r").readlines():
                line = self.re_matchComments.sub("", line)		# remove comments
                match = self.re_matchConfig.match(line)
                if match:
                    k = match.group(1)
                    v = match.group(2)
                    self[k] = eval(v)
        except Exception, e:
            print "Can't read the config file. Did you install it?\n"
            print e
            print "Reverting to defaults\n"
            self.setDefaults()
            self.saveConfig()
    #end loadConfig()

    def saveConfig(self):
        outfile = open(self.configFile, "w")
        outfile.write("# auto generated, manual changes may be overwritten\n")
        for k in self.keys():
            outfile.write( "%s=%s\n" % (k, repr(self[k])) )
        outfile.close()
    #end save()


def human_readable_size( num ):
    for x in ['bytes','KB','MB','GB','TB']:
        if num < 1024.0:
            return "%3.1f%s" % (num, x)
        num /= 1024.0

def expandName( raw, ext, base_dir=ANKIMINI_PATH+os.sep+"decks" ):
    if raw is None: return None
    if ext is None: ext=''
    if raw[0] == '/':
        canonical = raw
    else:
        canonical = os.path.join(base_dir, raw)

    if canonical[-len(ext):] != ext:
        canonical += ext

    return canonical
# expandName()

def openDeck(deckPath=None):
    global config
    try:
        if deckPath is None:
            deckPath = config['DECK_PATH']
        deckPath = expandName(deckPath, '.anki')
        print "open deck.. " + deckPath
        if not os.path.exists(deckPath):
            raise ValueError("Couldn't find deck %s" % (deckPath,) )
        deck = ds.Deck(deckPath, backup=False)
        deck.s.execute("pragma cache_size = 1000")
    except Exception, e:
        print "Error loading deck"
        print e
        deck = None
    return deck
#end openDeck()

def switchDeck( olddeck, newdeck_name ):
    global config
    newdeck_path = expandName(newdeck_name, '.anki')
    if olddeck:
        olddeck.save()
        if newdeck_path == olddeck.path:
            raise Exception( "Deck %s already active." % ( newdeck_path, ) )
        print "switching from %s to %s" % ( olddeck.path, newdeck_path )

    newdeck = openDeck(newdeck_path)
    if olddeck:
        olddeck.close()
    return newdeck
# switchDeck()


##################

class Handler(SimpleHTTPRequestHandler):

    local_css=None

    def __init__(self, *args, **kwargs):
        SimpleHTTPRequestHandler.__init__(self, *args, **kwargs)

    def setup(self):
       SimpleHTTPRequestHandler.setup(self)
       if config.get('DEBUG_NETWORK') == True:
           class FileProxy:
               file=None
               def __init__(self, file):
                   self.file=file
               def write(self,data):
                   print data
                   self.file.write(data)
               def __getattr__(self, name):
                   return getattr(self.file, name)
               def __setattr__(self, name, value):
                   if name in dir(self): self.__dict__[name] = value
                   else: setattr(self.file, name, value)

           self.wfile = FileProxy(self.wfile)

    def _outer(self):
        return """
<html>
<head>
<title>Anki</title>
<link rel="apple-touch-icon" href="http://ichi2.net/anki/anki-iphone-logo.png"/>
<meta name="viewport" content="user-scalable=yes, width=device-width,
  initial-scale=0.6667" />
<script type="text/javascript" language="javascript">
<!--
window.addEventListener("load", function() { setTimeout(loaded, 100) }, false);
function loaded() {
window.scrollTo(0, 1); // pan to the bottom, hides the location bar
}
//-->
</script>
</head>
<body style="margin-left: 0; margin-top: 0; margin-right: 0">
<iframe src="/question" width=470 frameborder=0 height=700>
</iframe>
</body></html>"""

    def _top(self):
        global config
        if deck and deck.modifiedSinceSave():
            saveClass="medButtonRed"
        else:
            saveClass="medButton"
        if deck and currentCard and deck.s.scalar(
            "select 1 from facts where id = :id and tags like '%marked%'",
            id=currentCard.factId):
            markClass = "medButtonRed"
        else:
            markClass = "medButton"
        if self.errorMsg:
            self.errorMsg = '<p style="color: red">' + self.errorMsg + "</p>"
        if deck:
            stats = self.getStats()
        else:
            stats = ("","")

        css =""
        if self.local_css is None:
            cssFile = expandName( config.get('LOCAL_CSS_FILE'), '.css' )
            try:
                f = open(cssFile,"r")
                self.local_css = f.read()
                f.close()
            except:
                self.local_css = ""
        use_local_css = config.get('USE_LOCAL_CSS', False)
        if use_local_css:
            css = self.local_css
        elif deck:
            css = deck.css
        if currentCard and deck and not use_local_css:
            background = deck.s.scalar(
                "select lastFontColour from cardModels where id = :id",
                id=currentCard.cardModelId)
        else:
            background = "#ffffff"
        return """
<html>
<head>
<style>
.bigButton
{ font-size: 24px; width: 445px; height: 130px; padding: 5px; border: 2px solid #7F7F7F; background-color: #E2E2E2; -webkit-border-radius: 40px; background: -webkit-gradient(linear, left top, left bottom, from(#fefefe), to(#cccccc)); }
.easeButton
{ font-size: 18px; width: 105px; height: 130px; padding: 5px; border: 2px solid #7F7F7F; background-color: #E2E2E2; -webkit-border-radius: 40px; background: -webkit-gradient(linear, left top, left bottom, from(#fefefe), to(#cccccc)); }
.easeButtonB
{ font-size: 18px; width: 170px; height: 180px; padding: 5px; border: 2px solid #7F7F7F; background-color: #E2E2E2; -webkit-border-radius: 40px; background: -webkit-gradient(linear, left top, left bottom, from(#fefefe), to(#cccccc)); }
.medButton
{ font-size: 18px; width: 100px; height: 40px; padding: 5px;}
.medButtonRed
{ font-size: 18px; width: 100px; height: 40px; padding: 5px; color: #FF0000; }
.q
{ font-size: 30px; }
.a
{ font-size: 30px; }
.qa-area
{ min-height: 240px; }
	body { margin-top: 0px; margin-left: 15px; padding: 0px; font-family: arial, helvetica; }
%s
</style>
</head>
<body id="inner_top">
<table width="100%%">
<tr valign=middle><td align=left>%s</td>
<td align=right>%s</td></table>
<table width="100%%"><tr valign=middle><td align=left>
<form action="/save" method="get">
<input class="%s" type="submit" class="button" value="Save">
</form></td>
<td align=left>
<form action="/mark" method="get">
<input class="%s" type="submit" class="button" value="Mark">
</form></td>
<td align=right>
<form action="/replay" method="get">
<input class="medButton" type="submit" class="button" value="Replay">
</form></td>
<td align=right>
<form action="/sync" method="get">
<input class="medButton" type="submit" class="button" value="Sync">
</form></td>
<td align=right>
</tr></table>
%s
<div style='background: %s'>
""" % (css, stats[0], stats[1], saveClass,
       markClass, self.errorMsg, background)

    _bottom = """
<br />
<table width="100%%"><tr valign=middle>
<td align=left>
<form action="/config" method="get">
<input class="medButton" type="submit" class="button" value="Config">
</form></td>
<td align=left>
<form action="/local" method="get">
<input class="medButton" type="submit" class="button" value="Local">
</form></td>
<td align=right>
<form action="/personal" method="get">
<input class="medButton" type="submit" class="button" value="Online">
</form></td>
<td align=right>
<form action="/about" method="get">
<input class="medButton" type="submit" class="button" value="About">
</form></td>
</table>
</body>
</html>"""

    def flushWrite(self, msg):
        self.wfile.write(msg+"\n")
        self.wfile.flush()

    def lineWrite(self, msg):
        self.flushWrite("<br />"+msg)

######################

    def render_get_config(self):
        global config
        buffer = """
		<html>
		<head><title>Config</title></head>
		<script type="text/javascript" language="javascript">
		<!--
		function setCssToggle() {
                        c = document.getElementById('localcss');
                        var fileInput = document.getElementById('cssfile');
			fileInput.disabled = !c.checked
		}
                window.onload=setCssToggle;
		//-->
		</script>
		<style>
		.page-header { background-color: #8FA1B9; border-bottom-style: solid; border-bottom-width: 1.5px; border-bottom-color: #2D3642; border-top-style: solid; border-top-width: 1.5px; border-top-color: #CDD5DF; color: #FFFFFF;
			font-family: Arial, Helvetica, sans-serif; height: 51px; font-size: 30px; font-weight: bold; text-align: center; padding-top: 15px; text-shadow: 0 -1.5px 1.2px #5D6773, 0 1.5px 1.2px #A4B2C4; background: -webkit-gradient(linear, left top, left bottom, from(#B0BCCD), to(#6D84A2), color-stop(0.5, #889BB3), color-stop(0.5, #8195AF)); }
		</style>
		<body style="font-family: arial, helvetica; margin-left: 0px; margin-right: -10px; margin-top: 0px">
		<div class="page-header">Config</div>
		<div style="margin-left: 15; margin-right: 15; margin-top: 15">
		 <form action="/config" method="POST">
		  <fieldset><legend>Sync details</legend>
		   <label for="username">User name</label> <input id="username" type="text" name="username" value="%s" autocorrect="off" autocapitalize="off" /> <br />
		   <label for="password">Password</label>  <input id="password" type="password" name="password" value="%s" autocorrect="off" autocapitalize="off" />
		  </fieldset>
		  <fieldset><legend>Deck</legend>
		   <label for="deckpath">Deck</label>  <input id="deckpath" type="text" name="deckpath" value="%s" autocorrect="off" autocapitalize="off" />
                   <em>(change doesn't take effect until a server restart)</em>
		  </fieldset>
		  <fieldset><legend>Display</legend>
		   <label for="localcss">Override deck CSS?</label>  <input id="localcss" type="checkbox" name="localcss" value="localcss" %s onclick="setCssToggle()" /><br />
		   <label for="cssfile">CSS Filename</label>  <input id="cssfile" type="text" name="cssfile" value="%s" /><br />
		   <label for="dispint">Display Interval</label>  <input id="dispint" type="checkbox" name="dispint" value="dispint" %s /><br />
		   <label for="dispdiv">Display Divider</label>  <input id="dispdiv" type="checkbox" name="dispdiv" value="dispdiv" %s />
		  </fieldset>
		  <fieldset><legend>Misc details</legend>
		   <label for="play">Play command</label>  <input id="play" type="text" name="play" value="%s" autocorrect="off" autocapitalize="off" /> <br />
		   <label for="port">Server port</label>  <input id="port" type="text" name="port" value="%s" autocorrect="off" autocapitalize="off" />
                   <em>(port change doesn't take effect until a server restart)</em><br />
		  </fieldset>
		  <fieldset class="submit">
		   <input type="submit" class="button" value="Save Config">
		  </fieldset>
		 </form>
		 <br /><a href="/question#inner_top">return</a>
		</div>
		</body>
		</html>
        """ % ( config.get('SYNC_USERNAME',''), config.get('SYNC_PASSWORD',''), config.get('DECK_PATH',''),
                 'checked="checked"' if config.get('USE_LOCAL_CSS') else '', config.get('LOCAL_CSS_FILE',''),
                 'checked="checked"' if config.get('DISPLAY_INTERVAL') else '',
                 'checked="checked"' if config.get('DISPLAY_DIVIDER') else '',
                 config.get('PLAY_COMMAND',''), config.get('SERVER_PORT',''))

        return buffer
####################### end render_get_config

    def render_post_config(self, params):
        global config
        buffer = "<html><head><title>Config ...</title></head>"
        errorMsg = ""
        try:
            username = unicode(params['username'][0], 'utf-8')
            password = unicode(params['password'][0], 'utf-8')
            deckpath = unicode(params['deckpath'][0], 'utf-8')
            port = int(params['port'][0])
            play = unicode(params['play'][0], 'utf-8')

            dispint = params.has_key('dispint')
            dispdiv = params.has_key('dispdiv')
            localcss = params.has_key('localcss')
            if localcss:
                cssfile  = unicode(params['cssfile' ][0], 'utf-8')
                # force re-read of local css file next time around
                self.local_css=None
            else:
                cssfile = config.get('LOCAL_CSS_FILE','')

            config.update( { 'SYNC_USERNAME': username, 'SYNC_PASSWORD': password,
                              'USE_LOCAL_CSS': localcss, 'LOCAL_CSS_FILE': cssfile,
                              'SERVER_PORT': port, 'PLAY_COMMAND': play,
                              'DISPLAY_INTERVAL': dispint, 'DISPLAY_DIVIDER': dispdiv} )
            config.saveConfig()
            try:
                global deck
                deck = switchDeck( deck, deckpath )
                if deck:
                    config['DECK_PATH']=deck.path
                    config.saveConfig()
                    deck.reset()
                else:
                    errorMsg += "<br /><br /><b>Deck didn't change</b>: " 
                    if os.path.exists(deckpath):
                        errorMsg += "New deck file exists but failed to open.  Could be corrupt?"
                    else:
                        errorMsg += ("Deck file does not exist.  Download one from Anki online, or copy a deck from your PC to %s on this device." % ANKIMINI_PATH)
            except Exception, e:
                errorMsg += "<br /><br />Unexpected exception trying to change deck: " + str(e)

            obscured = '*' * len(password)
            buffer += """
		<em>Config saved</em> <br />
		Username = %s <br />
		Password = %s <br />
                Override deck CSS = %s <br />
                Local CSS file = %s <br />
                Display interval = %s <br />
                Display divider = %s <br />
                Port = %d <br />
                Play = %s <br />
                %s
		""" % ( username, obscured, localcss, cssfile, dispint, dispdiv, port, play, errorMsg )
        except Exception, e:
            buffer += "<em>Config save may have failed!  Please check the values and try again</em><br />"
            buffer += str(e)

        buffer += """
		<br /><a href="/question#inner_top">return</a>
		</body></html>
		"""

        return buffer
####################### end render_post_config

    def render_get_local(self):
        import glob
        buffer = """
		<html>
		<head><title>List local decks</title>
		<style>
		.page-header { background-color: #8FA1B9; border-bottom-style: solid; border-bottom-width: 1.5px; border-bottom-color: #2D3642; border-top-style: solid; border-top-width: 1.5px; border-top-color: #CDD5DF; color: #FFFFFF;
			font-family: Arial, Helvetica, sans-serif; height: 51px; font-size: 30px; font-weight: bold; text-align: center; padding-top: 15px; text-shadow: 0 -1.5px 1.2px #5D6773, 0 1.5px 1.2px #A4B2C4; background: -webkit-gradient(linear, left top, left bottom, from(#B0BCCD), to(#6D84A2), color-stop(0.5, #889BB3), color-stop(0.5, #8195AF)); }
		</style>
		</head>
		<body style="font-family: arial, helvetica; margin-left: 0px; margin-right: -10px; margin-top: 0px">
		<div class="page-header">Local Decks</div>
		<div style="margin-left: 15; margin-right: 15; margin-top: 15">
		"""
        try:
            deckList = glob.glob(os.path.join(ANKIMINI_PATH+os.sep+"decks","*.anki"))
            if deckList is None or len(deckList)==0:
                buffer += "<em>You have no local decks!<br />Download one from Anki online, or copy deck files from your PC to %s on this device." % ANKIMINI_PATH
            else:
                buffer += '<table width="80%%" cellspacing="10"><col width="80%%" />'
                for p in deckList:
                    import stat
                    bytes=os.stat(p)[stat.ST_SIZE]
                    name=os.path.basename(p)[:-5]
                    buffer += '<tr><td><a href="/switch?d=%s&i=y">%s</a></td><td>%s</td></tr>' % ( name, name, human_readable_size(bytes) )
                buffer += "</table>"
        except Exception, e:
            buffer += "<em>Error listing files!</em><br />" + str(e)

        buffer += """
		<br /><a href="/question#inner_top">return</a>
		</div>
		</body>
		</html>
	        """

        return buffer
####################### end render_get_local

    def render_get_personal(self):
        global config
        buffer = """
		<html>
		<head><title>List personal decks</title>
		<style>
		.page-header { background-color: #8FA1B9; border-bottom-style: solid; border-bottom-width: 1.5px; border-bottom-color: #2D3642; border-top-style: solid; border-top-width: 1.5px; border-top-color: #CDD5DF; color: #FFFFFF;
			font-family: Arial, Helvetica, sans-serif; height: 51px; font-size: 30px; font-weight: bold; text-align: center; padding-top: 15px; text-shadow: 0 -1.5px 1.2px #5D6773, 0 1.5px 1.2px #A4B2C4; background: -webkit-gradient(linear, left top, left bottom, from(#B0BCCD), to(#6D84A2), color-stop(0.5, #889BB3), color-stop(0.5, #8195AF)); }
		</style>
		</head>
		<body style="font-family: arial, helvetica; margin-left: 0px; margin-right: -10px; margin-top: 0px">
		<div class="page-header">Online Personal Decks</div>
		<div style="margin-left: 15; margin-right: 15; margin-top: 15">
		Please select one to download it...
		"""
        try:
            proxy = HttpSyncServerProxy(config.get('SYNC_USERNAME'), config.get('SYNC_PASSWORD'))
            deckList = proxy.availableDecks()
            if deckList is None or len(deckList)==0:
                buffer += "<br /><em>You have no online decks!</em>"
            else:
                buffer += '<table width="100%%" cellspacing="10">'
                for d in deckList:
                   	buffer += '<tr height><td><a href="/download?deck=%s">%s</a></td></tr>' % ( d, d )
                buffer += "</table>"
        except:
            buffer += "<br /><em>Can't connect - check username/password</em>"

        buffer += """
		<br /><a href="/question#inner_top">return</a>
		</div>
		</body>
		</html>
	        """

        return buffer
####################### end render_get_personal

    def render_get_download(self, params):
        global deck
        global config
        global ANKIMINI_PATH

        import tempfile

        name = params["deck"]
        self.wfile.write( """
		<html>
		<head><title>Downloading %s ...</title></head>
		<body style="font-family: arial, helvetica;">
		""" % ( name ) )
        buffer=""

        tmp_dir=None
        try:
            if deck:
                deck.save()
            local_deck = expandName(name, '.anki')
            if os.path.exists(local_deck):
                raise Exception("Local deck %s already exists.  You can't overwrite, sorry!" % (name,))

            tmp_dir = unicode(tempfile.mkdtemp(dir=ANKIMINI_PATH, prefix="anki"), sys.getfilesystemencoding())
            tmp_deck = expandName(name, '.anki', tmp_dir)

            newdeck = ds.Deck(tmp_deck)
            newdeck.s.execute("pragma cache_size = 1000")
            newdeck.modified = 0
            newdeck.s.commit()
            newdeck.lastLoaded = newdeck.modified

	    newdeck = self.syncDeck( newdeck )
            newdeck.save()

            if deck:
                deck.close()
                deck = None
            newdeck.close()
            os.rename(tmp_deck, local_deck)
            config['DECK_PATH']=local_deck
            config.saveConfig()
            deck = openDeck()

        except Exception, e:
            buffer += "<em>Download failed!</em><br />"
            buffer += str(e)
            print "render_get_download(): exception: " + str(e)
            import traceback
            traceback.print_exc()
        finally:
            if tmp_dir:
                try:
                    os.remove(tmp_deck)
                    os.remove(tmp_deck+"-journal")
                except:
                    pass
                try:
                    os.rmdir(tmp_dir)
                except:
                    pass

        buffer += """
		<br />
		<a href="/question#inner_top">return</a>
		</body></html>
		"""

        return buffer
####################### end render_get_download

    def render_get_about(self):
        global deck
        global config

        obscured='*' * len(config.get('SYNC_PASSWORD',''))
        buffer = """
            <html>
            <head><title>About</title>
			<style>
			.page-header { background-color: #8FA1B9; border-bottom-style: solid; border-bottom-width: 1.5px; border-bottom-color: #2D3642; border-top-style: solid; border-top-width: 1.5px; border-top-color: #CDD5DF; color: #FFFFFF;
				font-family: Arial, Helvetica, sans-serif; height: 51px; font-size: 30px; font-weight: bold; text-align: center; padding-top: 15px; text-shadow: 0 -1.5px 1.2px #5D6773, 0 1.5px 1.2px #A4B2C4; background: -webkit-gradient(linear, left top, left bottom, from(#B0BCCD), to(#6D84A2), color-stop(0.5, #889BB3), color-stop(0.5, #8195AF)); }
			</style>
			</head>
            <body style="font-family: arial, helvetica; margin-left: 0px; margin-right: -10px; margin-top: 0px">
            <div class="page-header">About</div>
            <div style="margin-left: 15; margin-right: 15; margin-top: 15">
            <h1>AnkiMini v%s</h1>
            <h2>Currently Loaded Deck</h2>
            %s
            <h2>Versions</h2>
            <table width="100%%">
		<tr><th align="left">Component</th><th align="left">Version</th></tr>
                <tr><td>Ankimini</td><td>%s</td></tr>
                <tr><td>libanki</td><td>%s</td></tr>
            </table>
            <h2>Current config</h2>
            <table width="100%%">
                <tr><td>Config path</td><td>%s</td></tr>
                <tr><td>Deck path</td><td>%s</td></tr>
                <tr><td>Sync user</td><td>%s</td></tr>
                <tr><td>Sync pass</td><td>%s</td></tr>
                <tr><td>Override deck CSS</td><td>%s</td></tr>
                <tr><td>Local CSS file</td><td>%s</td></tr>
                <tr><td>Display interval</td><td>%s</td></tr>
                <tr><td>Display divider</td><td>%s</td></tr>
                <tr><td>Server port</td><td>%s</td></tr>
                <tr><td>Play command</td><td>%s</td></tr>
            </table>
            <p>For more info on Anki, visit the <a href="http://ichi2.net/anki">home page</a></p>
	    <br /><a href="/question#inner_top">return</a>
            </div>
            </body>
            </html>
        """ % ( VERSION_ANKIMINI,
		deck.path if deck is not None else 'None',
		VERSION_ANKIMINI, VERSION_LIBANKI,
		config.configFile, config.get('DECK_PATH'), config.get('SYNC_USERNAME'), obscured,
		config.get('USE_LOCAL_CSS'), config.get('LOCAL_CSS_FILE'),
		config.get('DISPLAY_INTERVAL'), config.get('DISPLAY_DIVIDER'), config.get('SERVER_PORT'), config.get('PLAY_COMMAND'),
              )

        return buffer
####################### end render_get_about

    def render_get_check(self, quick):
        global deck
        global config

	quick = self.path.startswith("/check_quick")
        self.wfile.write("""
        <html><head><title>Checking deck</title></head> 
        Performing a deck database check...
        """)

        if not deck:
            return "Error: no deck is open, nothing to check!</body></html>"

        # hack to get safari to render immediately!
        self.flushWrite("<!--" + " "*1024 + "-->")

	# this can take a long time ... ensure the client doesn't timeout before we finish
	from threading import Event, Thread
	ping_event = Event()
        def ping_client( s = self.wfile, ev=ping_event ):
            while 1:
                ev.wait(3)
                if ev.isSet():
                    return
                s.write(".<!--\n-->")
                s.flush()
	ping_thread = Thread(target=ping_client)
	ping_thread.start()

        if quick:
            self.flushWrite("<br/>Doing quick check ...")
        else:
            self.flushWrite("<br/>Doing FULL check, please be patient...")
        ret = deck.fixIntegrity(quick)
        buffer = "<br/>Result: <br/>" 

        if ret == "ok":
            buffer += ret
        else:
            buffer += ("Problems found:<br>%s" % ret)

        buffer += """<br /><a href="/question#inner_top">return</a> </body> </html>"""
        
        # turn off client ping
        ping_event.set()
        ping_thread.join(5)

        return buffer

####################### end render_get_check


    def do_POST(self):
        history.append(self.path)
        serviceStart = time.time()
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()

        length = int(self.headers.getheader('content-length'))
        qs = self.rfile.read(length)
        params = cgi.parse_qs(qs, keep_blank_values=1)

        if self.path.startswith("/config"):
	    buffer = self.render_post_config(params)

        self.wfile.write(buffer.encode("utf-8") + "\n")
        print "service time", time.time() - serviceStart
    #end do_POST()

    def do_GET(self):
        global config
        self.played = False
        lp = self.path.lower()
        def writeImage():
            try:
                self.wfile.write(open(os.path.join(deck.mediaDir(), lp[1:])).read())
            except:
                pass
        for (ext, type) in ((".jpg", "image/jpeg"),
                            (".jpeg", "image/jpeg"),
                            (".png", "image/png"),
                            (".gif", "image/gif"),
                            (".tif", "image/tiff"),
                            (".tiff", "image/tiff"),
                            (".png", "image/png")):
            if lp.endswith(ext):
                self.send_response(200)
                self.send_header("Content-type", type)
                self.end_headers()
                writeImage()
                return
        history.append(self.path)
        serviceStart = time.time()
        global currentCard, deck
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        # cgi parms
        try:
            (path, qs) = self.path.split("?")
        except ValueError:
            qs = ""
        query = cgi.parse_qs(qs)
        for k in query:
            query[k] = query[k][0]
        q = query.get("q", None)
        mod = query.get("mod", None)
        self.errorMsg = ""
        buffer = u""

        if self.path.startswith("/switch"):
            new = query.get("d", config.get('DECK_PATH',''))
            try:
                deck = switchDeck(deck, new)
                config['DECK_PATH']=deck.path
                config.saveConfig()
                deck.reset()
            except Exception, e:
                self.errorMsg = str(e)
            if query.get("i"):
                self.path="/question#inner_top"
            else:
                self.path="/"

        if self.path == "/":
            # refresh
            if deck:
                deck.reset()
            self.flushWrite(self._outer())
        elif deck and self.path.startswith("/save"):
            deck.save()
            self.path = "/question#inner_top"
        elif deck and self.path.startswith("/mark"):
            if currentCard:
                f = deck.s.query(Fact).get(currentCard.factId)
                if "marked" in f.tags.lower():
                    t = parseTags(f.tags)
                    t.remove("Marked")
                    f.tags = joinTags(t)
                else:
                    f.tags = joinTags(parseTags(
                        f.tags) + ["Marked"])
                f.setModified(textChanged=True, deck=deck)
                deck.updateFactTags([f.id])
                f.setModified()
                deck.flushMod()
                deck.s.flush()
                deck.s.expunge(f)
                history.pop()
            self.path = "/question#inner_top"
        elif deck and self.path.startswith("/replay"):
            if currentCard:
                self.prepareMedia(currentCard.question)
                self.prepareMedia(currentCard.answer)
                self.disableMedia()
                history.pop()
            self.path = "/question"
        elif deck and self.path.startswith("/sync"):
            deck.save()
            deck.lastLoaded = time.time()
            # syncing
            try:
                self.flushWrite("""
			<html><head>
			<meta name="viewport" content="user-scalable=yes, width=device-width, maximum-scale=0.6667" />
			</head><body style="font-family: arial, helvetica;">""")
                deck = self.syncDeck( deck )
                self.flushWrite('<br><a href="/question#inner_top">return</a>')
                self.flushWrite("</body></html>")
            except Exception, e:
                self.errorMsg = `traceback.format_exc()`
                self.path = "/question#inner_top"


        if self.path.startswith("/question"):
            if not deck:
                self.errorMsg = "No deck opened! Check config is correct."
                buffer += (self._top() + self._bottom)
            else:
                try: # most deck errors manifest in answering cards
                    # possibly answer old card
                    if (q is not None and
                        currentCard and mod == str(int(currentCard.modified))):
                        deck.answerCard(currentCard, int(q))
                    # get new card
                    currentCard = deck.getCard(orm=False)
                    if not currentCard:
                        buffer += (self._top() +
                                   deck.deckFinishedMsg() +
                                   self._bottom)
                    else:
                        buffer += (self._top() + ("""
<br>
<div class="qa-area">
<div class="q" %(divider)s>%(question)s<br /></div>
</div>
<br></div><br><form action="/answer" method="get" style="margin: 0px; padding: 0px;">
<table width="100%%">
<tr>
<td align=center><button class="bigButton" type="submit" class="button" value="Answer">Answer</button></td>
</tr>
</table>
</form>
""" % {
        "divider": 'style="border-bottom-style: solid; border-bottom-width: 1px; border-bottom-color: #7F7F7F;"' if config.get('DISPLAY_DIVIDER', False) else '',
        "question": self.prepareMedia(currentCard.htmlQuestion(align=False)),
        }))
                        buffer += (self._bottom)
                except Exception, e:
                    self.errorMsg = "An Exception was thrown processing the card."
                    buffer += (self._top() + ("""
<br/> <br/>
There may be a bug in ankimini or an error in this deck.  You can try either a
<b><a href="/check_quick">quick database check</a></b> or a 
<b><a href="/check">full database check</a></b> to try and fix it.
<br/> <br/>
Or, just try to <a href="/question#inner_top">reload</a> the page and see if
the problem magically goes away.
<p>The exception was:<pre>%s</pre>
""" % traceback.format_exc()))
                    buffer += (self._bottom)
        elif self.path.startswith("/answer"):
            if not currentCard:
                currentCard = deck.getCard(orm=False)
            c = currentCard
            buffer += (self._top() + """
<br>
<div class="qa-area">
<div class="q" %(divider)s>%(question)s<br /></div>
<div class="a">%(divider_two)s%(answer)s</div>
</div>
<br></div><br><form action="/question#inner_top" method="get" style="margin: 0px; padding: 0px;">
<input type="hidden" name="mod" value="%(mod)d">
<table width="100%%">
<tr>
""" % {
    "divider": 'style="border-bottom-style: solid; border-bottom-width: 1px; border-bottom-color: #7F7F7F;"' if config.get('DISPLAY_DIVIDER', False) else '',
    "divider_two": '<br />' if config.get('DISPLAY_DIVIDER', False) else '',
    "question": self.prepareMedia(c.htmlQuestion(align=False), auto=False),
    "answer": self.prepareMedia(c.htmlAnswer(align=False)),
    "mod": c.modified,
    })
            display_interval = config.get('DISPLAY_INTERVAL', False)
            if display_interval:
                ints = {}
                for i in range(1, 5):
                    ints[str(i)] = deck.nextIntervalStr(c, i, True)
                buffer += ("""
    <td align=center><button class="easeButton" type="submit" class="button" name="q"
    value="1">Soon</button></td>
    <td align=center><button class="easeButton" type="submit" class="button" name="q"
    value="2">%(2)s</button><br></td>
    <td align=center><button class="easeButton" type="submit" class="button" name="q"
    value="3">%(3)s</button></td>
    <td align=center><button class="easeButton" type="submit" class="button" name="q"
    value="4">%(4)s</button></td>
    """ % ints)
            else:
                buffer += ("""
    <td align=center><button class="easeButton" type="submit" class="button" name="q"
    value="1">Again</button></td>
    <td align=center><button class="easeButton" type="submit" class="button" name="q"
    value="2">Hard</button><br></td>
    <td align=center><button class="easeButton" type="submit" class="button" name="q"
    value="3">Good</button></td>
    <td align=center><button class="easeButton" type="submit" class="button" name="q"
    value="4">Easy</button></td>
    """)
            buffer += ("</tr></table></form>")
            buffer += (self._bottom)

        elif self.path.startswith("/config"):
	    buffer = self.render_get_config()
	elif self.path.startswith("/local"):
	    buffer = self.render_get_local()
	elif self.path.startswith("/personal"):
	    buffer = self.render_get_personal()
	elif self.path.startswith("/download"):
	    buffer = self.render_get_download(query)
	elif self.path.startswith("/about"):
	    buffer = self.render_get_about()
        elif self.path.startswith("/check"):
            buffer = self.render_get_check(deck)
		
        self.wfile.write(buffer.encode("utf-8") + "\n")
        print "service time", time.time() - serviceStart

########################

    def syncDeck(self, deck):
        try:
            proxy = HttpSyncServerProxy(config.get('SYNC_USERNAME'), config.get('SYNC_PASSWORD'))
            proxy.connect("ankimini")
        except:
            raise Exception("Can't sync: " + traceback.format_exc())
        if not proxy.hasDeck(deck.name()):
            raise Exception("Can't sync, no deck on server")
        if abs(proxy.timestamp - time.time()) > 60:
            raise Exception("Your clock is off by more than 60 seconds.<br>" \
                            "Syncing will not work until you fix this.")

        client = SyncClient(deck)
        client.setServer(proxy)
        # need to do anything?
        proxy.deckName = deck.name()
        print proxy.deckName
        if not client.prepareSync(0):
            raise Exception("Nothing to do")

	self.flushWrite("""<h1>Syncing deck</h1>
        <h2>%s</h2>
	<em>This could take a while with a big deck ... please be patient!</em>
	""" % (deck.path,) )

        # hack to get safari to render immediately!
        self.flushWrite("<!--" + " "*1024 + "-->")

	# this can take a long time ... ensure the client doesn't timeout before we finish
	from threading import Event, Thread
	ping_event = Event()
        def ping_client( s = self.wfile, ev=ping_event ):
            while 1:
                ev.wait(3)
                if ev.isSet():
                    return
                s.write(".<!--\n-->")
                s.flush()
	ping_thread = Thread(target=ping_client)
	ping_thread.start()

        # summary
        self.lineWrite("Fetching summary from server..")
        sums = client.summaries()
        needFull = client.needFullSync(sums)
        if needFull:
            self.lineWrite("Doing full sync..")
            client.fullSync()
        else:
            # diff
            self.lineWrite("Determining differences..")
            payload = client.genPayload(sums)
            # send payload
            pr = client.payloadChangeReport(payload)
            self.lineWrite("<br>" + pr + "<br>")
            self.lineWrite("Sending payload...")

        if needFull:
            deck = ds.Deck(deck.path, backup=False)
        else:
            res = client.server.applyPayload(payload)
            # apply reply
            self.lineWrite("Applying reply..")
            client.applyPayloadReply(res)
            try:
                client.server.finish()
            except:
                deck.s.rollback()
        # finished. save deck, preserving mod time
        self.lineWrite("Sync complete.")
        deck.reset()
        deck.lastLoaded = deck.modified
        deck.s.flush()
        deck.s.commit()

	# turn off client ping
	ping_event.set()
        ping_thread.join(5)

        return deck

    def getStats(self):
        s = deck.getStats(short=True)
        stats = (("T: %(dYesTotal)d/%(dTotal)d "
                 "(%(dYesTotal%)3.1f%%) "
                 "A: <b>%(gMatureYes%)3.1f%%</b>. ETA: <b>%(timeLeft)s</b>") % s)
        f = "<font color=#990000>%(failed)d</font>"
        r = "<font color=#000000>%(rev)d</font>"
        n = "<font color=#0000ff>%(new)d</font>"
        if currentCard:
            if currentCard.reps:
                if currentCard.successive:
                    r = "<u>" + r + "</u>"
                else:
                    f = "<u>" + f + "</u>"
            else:
                n = "<u>" + n + "</u>"
        stats2 = ("<font size=+2>%s+%s+%s</font>" % (f,r,n)) % s
        return (stats, stats2)

    def disableMedia(self):
        "Stop processing media for the rest of the request."
        self._disableMedia = True

    def prepareMedia(self, string, auto=True):
        class AudioThread(threading.Thread):
            def __init__(self, *args, **kwargs):
                self.toPlay = kwargs['toPlay']
                del kwargs['toPlay']
                threading.Thread.__init__(self, *args, **kwargs)
            def run(self):
                for f in self.toPlay:
                    os.system([config.get('PLAY_COMMAND')+" "+ f])
        toPlay = []
        for filename in mediaFiles(string):
            if auto and (filename.lower().endswith(".mp3") or
                         filename.lower().endswith(".wav")):
                if deck.mediaDir():
                    toPlay.append(os.path.join(deck.mediaDir(), filename))
                string = re.sub(re.escape(fullMatch), "", string)
        if getattr(self, "_disableMedia", None):
            return string
        self.played = toPlay
        at = AudioThread(toPlay=toPlay)
        at.start()
        return string

def run(server_class=HTTPServer,
        handler_class=Handler):
    global config
    server_address = ('127.0.0.1', config.get('SERVER_PORT',8000))		# explicit 127.0.0.1 so that delays aren't experienced if wifi/3g networks are not reliable
    httpd = server_class(server_address, handler_class)
    httpd.serve_forever()



######## main

if __name__ == '__main__':
    config = Config()
    config.loadConfig()
    try:
        deck = openDeck()
    except:
        deck = None
    currentCard = None
    history = []
    print "starting server on port %d" % config.get('SERVER_PORT',8000)
    run()


