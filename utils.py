import re
from types import ClassType
from os.path import join, abspath, dirname, split

import zope.interface
from zope.interface import providedBy
from zope.interface import implementedBy
from zope.component import getViewProviding

import OFS
import Globals
from Acquisition import aq_base, aq_inner, aq_parent
from DateTime import DateTime
from Products.Five import BrowserView as BaseView
from Products.Five.bridge import fromZ2Interface
from Products.CMFCore.utils import ToolInit as CMFCoreToolInit
from Products.CMFCore.utils import getToolByName
from Products.CMFPlone.browser.interfaces import IDefaultPage
from Products.CMFPlone.browser.interfaces import INavigationBreadcrumbs
from Products.CMFPlone.browser.interfaces import INavigationTabs
from Products.CMFPlone.browser.interfaces import INavigationTree
from Products.CMFPlone.UnicodeNormalizer import normalizeUnicode
from Products.CMFPlone.interfaces.Translatable import ITranslatable

import transaction

# Canonical way to get at CMFPlone directory
PACKAGE_HOME = Globals.package_home(globals())
WWW_DIR = join(PACKAGE_HOME, 'www')

# Log methods
from log import log
from log import log_exc
from log import log_deprecated

# Keep these here to not fully change the old API
# please use i18nl10n directly
from i18nl10n import utranslate
from i18nl10n import ulocalized_time
from i18nl10n import getGlobalTranslationService

_marker = ()

class BrowserView(BaseView):

    def __init__(self, context, request):
        self.context = [context]
        self.request = request

def parent(obj):
    return aq_parent(aq_inner(obj))

def context(view):
    return view.context[0]

def createBreadCrumbs(context, request):
    view = getViewProviding(context, INavigationBreadcrumbs, request)
    return view.breadcrumbs()

def createTopLevelTabs(context, request, actions=None):
    view = getViewProviding(context, INavigationTabs, request)
    return view.topLevelTabs(actions=actions)

def createNavTree(context, request, sitemap=False):
    view = getViewProviding(context, INavigationTree, request)
    return view.navigationTree(sitemap=sitemap)

def addToNavTreeResult(result, data):
    path = data['path']
    parentpath = '/'.join(path.split('/')[:-1])
    # Tell parent about self
    if result.has_key(parentpath):
        result[parentpath]['children'].append(data)
    else:
        result[parentpath] = {'children':[data]}
    # If we have processed a child already, make sure we register it
    # as a child
    if result.has_key(path):
        data['children'] = result[path]['children']
    result[path] = data

def isDefaultPage(obj, request, context=None):
    container = parent(obj)
    if not container:
        return False
    view = getViewProviding(container, IDefaultPage, request)
    if context is None:
        context = obj
    return view.isDefaultPage(obj, context)

def getDefaultPage(obj, request, context=None):
    # Short circuit if we are not looking at a Folder
    if not obj.isPrincipiaFolderish:
        return None
    view = getViewProviding(obj, IDefaultPage, request)
    if context is None:
        context = obj
    return view.getDefaultPage(context)

def isIDAutoGenerated(context, id):
    # In 2.1 non-autogenerated is the common case, caught exceptions are
    # expensive, so let's make a cheap check first
    if id.count('.') != 2:
        return False

    pt = getToolByName(context, 'portal_types')
    portaltypes = pt.objectIds()
    portaltypes.extend([pt.lower() for pt in portaltypes])

    try:
        obj_type, date_created, random_number = id.split('.')
        type = ' '.join(obj_type.split('_'))
        # New autogenerated ids may have a lower case portal type
        if (type in portaltypes and
            DateTime(date_created) and
            float(random_number)):
            return True
    except (ValueError, AttributeError, IndexError, DateTime.DateTimeError):
        pass

    return False

def lookupTranslationId(obj, page, ids):
    implemented = ITranslatable.isImplementedBy(obj)
    if not implemented or implemented and not obj.isTranslation():
        pageobj = getattr(obj, page, None)
        if (pageobj is not None and
            ITranslatable.isImplementedBy(pageobj)):
            translation = pageobj.getTranslation()
            if (translation is not None and
                ids.has_key(translation.getId())):
                page = translation.getId()
    return page

def pretty_title_or_id(context, obj, empty_value=_marker):
    """Return the best possible title or id of an item, regardless
    of whether obj is a catalog brain or an object, but returning an
    empty title marker if the id is not set (i.e. it's auto-generated).
    """
    if safe_hasattr(obj, 'aq_explicit'):
        obj = obj.aq_explicit
    title = getattr(obj, 'Title', None)
    if safe_callable(title):
        title = title()
    if title:
        return title
    item_id = getattr(obj, 'getId', None)
    if safe_callable(item_id):
        item_id = item_id()
    if item_id and not isIDAutoGenerated(context, item_id):
        return item_id
    if empty_value is _marker:
        empty_value = getEmptyTitle(context)
    return empty_value

def getSiteEncoding(context):
    default = 'utf-8'
    pprop = getToolByName(context, 'portal_properties')
    site_props = getToolByName(pprop, 'site_properties', None)
    if site_props is None:
        return default
    return site_props.getProperty('default_charset', default)

def portal_utf8(context, str, errors='strict'):
    charset = getSiteEncoding(context)
    if charset.lower() in ('utf-8', 'utf8'):
        # Test
        unicode(str, 'utf-8', errors)
        return str
    else:
        return unicode(str, charset, errors).encode('utf-8', errors)

def utf8_portal(context, str, errors='strict'):
    charset = getSiteEncoding(context)
    if charset.lower() in ('utf-8', 'utf8'):
        # Test
        unicode(str, 'utf-8', errors)
        return str
    else:
        return unicode(str, 'utf-8', errors).encode(charset, errors)

def getEmptyTitle(context, translated=True):
    empty = utf8_portal(context, '\x5b\xc2\xb7\xc2\xb7\xc2\xb7\x5d', 'ignore')
    if translated:
        trans = getToolByName(context, 'translation_service')
        empty = trans.utranslate(domain='plone', msgid='title_unset',
                                 default=empty)
    return empty

def typesToList(context):
    ntp = getToolByName(context, 'portal_properties').navtree_properties
    ttool = getToolByName(context, 'portal_types')
    bl = ntp.getProperty('metaTypesNotToList', ())
    bl_dict = {}
    for t in bl:
        bl_dict[t] = 1
    all_types = ttool.listContentTypes()
    wl = [t for t in all_types if not bl_dict.has_key(t)]
    return wl

def normalizeString(text, context=None, encoding=None):
    assert context or encoding, 'Either context or encoding must be provided'
    # Make sure we are dealing with a stringish type
    if not isinstance(text, basestring):
        # Catch the special None case or we would return 'none' evaluating
        # to True, which is totally unexpected
        # XXX This seems to break the autogenerated ids, reverting
        #if text is None:
        #    return None

        # This most surely ends up in something the user does not expect
        # to see. But at least it does not break.
        text = repr(text)

    # Make sure we are dealing with a unicode string
    if not isinstance(text, unicode):
        if encoding is None:
            encoding = getSiteEncoding(context)
        text = unicode(text, encoding)

    text = text.lower()
    text = text.strip()
    text = normalizeUnicode(text)

    base = text
    ext  = ""

    m = re.match(r"^(.+)\.(\w{,4})$", text)
    if m is not None:
        base = m.groups()[0]
        ext  = m.groups()[1]

    base = re.sub(r"[\W\-]+", "-", base)
    base = re.sub(r"^\-+",    "",  base)
    base = re.sub(r"\-+$",    "",  base)

    if ext != "":
        base = base + "." + ext
    return base

class IndexIterator:
    __allow_access_to_unprotected_subobjects__ = 1

    def __init__(self, upper=100000, pos=0):
        self.upper=upper
        self.pos=pos

    def next(self):
        if self.pos <= self.upper:
            self.pos += 1
            return self.pos
        raise KeyError, 'Reached upper bounds'


class ToolInit(CMFCoreToolInit):

    def getProductContext(self, context):
        name = '_ProductContext__prod'
        return getattr(context, name, getattr(context, '__prod', None))

    def getPack(self, context):
        name = '_ProductContext__pack'
        return getattr(context, name, getattr(context, '__pack__', None))

    def getIcon(self, context, path):
        pack = self.getPack(context)
        icon = None
        # This variable is just used for the log message
        icon_path = path
        try:
            icon = Globals.ImageFile(path, pack.__dict__)
        except (IOError, OSError):
            # Fallback:
            # Assume path is relative to CMFPlone directory
            path = abspath(join(PACKAGE_HOME, path))
            try:
                icon = Globals.ImageFile(path, pack.__dict__)
            except (IOError, OSError):
                # if there is some problem loading the fancy image
                # from the tool then  tell someone about it
                log(('The icon for the product: %s which was set to: %s, '
                     'was not found. Using the default.' %
                     (self.product_name, icon_path)))
        return icon

    def initialize(self, context):
        """ Wrap the CMFCore Tool Init method """
        CMFCoreToolInit.initialize(self, context)
        for tool in self.tools:
            # Get the icon path from the tool
            path = getattr(tool, 'toolicon', None)
            if path is not None:
                pc = self.getProductContext(context)
                if pc is not None:
                    pid = pc.id
                    name = split(path)[1]
                    icon = self.getIcon(context, path)
                    if icon is None:
                        # Icon was not found
                        return
                    icon.__roles__ = None
                    tool.icon = 'misc_/%s/%s' % (self.product_name, name)
                    misc = OFS.misc_.misc_
                    Misc = OFS.misc_.Misc_
                    if not hasattr(misc, pid):
                        setattr(misc, pid, Misc(pid, {}))
                    getattr(misc, pid)[name] = icon


def _createObjectByType(type_name, container, id, *args, **kw):
    """Create an object without performing security checks

    invokeFactory and fti.constructInstance perform some security checks
    before creating the object. Use this function instead if you need to
    skip these checks.

    This method uses some code from
    CMFCore.TypesTool.FactoryTypeInformation.constructInstance
    to create the object without security checks.
    """
    id = str(id)
    typesTool = getToolByName(container, 'portal_types')
    fti = typesTool.getTypeInfo(type_name)
    if not fti:
        raise ValueError, 'Invalid type %s' % type_name

    # we have to do it all manually :(
    p = container.manage_addProduct[fti.product]
    m = getattr(p, fti.factory, None)
    if m is None:
        raise ValueError, ('Product factory for %s was invalid' %
                           fti.getId())

    # construct the object
    m(id, *args, **kw)
    ob = container._getOb( id )

    return fti._finishConstruction(ob)


def safeToInt(value):
    """Convert value to integer or just return 0 if we can't"""
    try:
        return int(value)
    except ValueError:
        return 0

release_levels = ('alpha', 'beta', 'candidate', 'final')
rl_abbr = {'a':'alpha', 'b':'beta', 'rc':'candidate'}

def versionTupleFromString(v_str):
    """Returns version tuple from passed in version string"""
    regex_str = "(^\d+)[.]?(\d*)[.]?(\d*)[- ]?(alpha|beta|candidate|final|a|b|rc)?(\d*)"
    v_regex = re.compile(regex_str)
    match = v_regex.match(v_str)
    if match is None:
        v_tpl = None
    else:
        groups = list(match.groups())
        for i in (0, 1, 2, 4):
            groups[i] = safeToInt(groups[i])
        if groups[3] is None:
            groups[3] = 'final'
        elif groups[3] in rl_abbr.keys():
            groups[3] = rl_abbr[groups[3]]
        v_tpl = tuple(groups)
    return v_tpl

def getFSVersionTuple():
    """Reads version.txt and returns version tuple"""
    vfile = "%s/version.txt" % PACKAGE_HOME
    v_str = open(vfile, 'r').read().lower()
    return versionTupleFromString(v_str)


def transaction_note(note):
    """Write human legible note"""
    T=transaction.get()
    if isinstance(note, unicode):
        # Convert unicode to a regular string for the backend write IO.
        # UTF-8 is the only reasonable choice, as using unicode means
        # that Latin-1 is probably not enough.
        note = note.encode('utf-8', 'replace')

    if (len(T.description)+len(note))>=65535:
        log('Transaction note too large omitting %s' % str(note))
    else:
        T.note(str(note))


def base_hasattr(obj, name):
    """Like safe_hasattr, but also disables acquisition."""
    return safe_hasattr(aq_base(obj), name)


def safe_hasattr(obj, name, _marker=object()):
    """Make sure we don't mask exceptions like hasattr().

    We don't want exceptions other than AttributeError to be masked,
    since that too often masks other programming errors.
    Three-argument getattr() doesn't mask those, so we use that to
    implement our own hasattr() replacement.
    """
    return getattr(obj, name, _marker) is not _marker


def safe_callable(obj):
    """Make sure our callable checks are ConflictError safe."""
    if safe_hasattr(obj, '__class__'):
        if safe_hasattr(obj, '__call__'):
            return True
        else:
            return isinstance(obj, ClassType)
    else:
        return callable(obj)

def tuplize(value):
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)

def _detuplize(interfaces, append):
    if isinstance(interfaces, (tuple, list)):
        for sub in interfaces:
            _detuplize(sub, append)
    else:
        append(interfaces)

def flatten(interfaces):
    flattened = []
    _detuplize(interfaces, flattened.append)
    return tuple(flattened)

def directlyProvides(obj, *interfaces):
    # convert any Zope 2 interfaces to Zope 3 using fromZ2Interface
    interfaces = flatten(interfaces)
    normalized_interfaces = []
    for i in interfaces:
        try:
            i = fromZ2Interface(i)
        except ValueError: # already a Zope 3 interface
            pass
        assert issubclass(i, zope.interface.Interface)
        normalized_interfaces.append(i)
    return zope.interface.directlyProvides(obj, *normalized_interfaces)

def classImplements(class_, *interfaces):
    # convert any Zope 2 interfaces to Zope 3 using fromZ2Interface
    interfaces = flatten(interfaces)
    normalized_interfaces = []
    for i in interfaces:
        try:
            i = fromZ2Interface(i)
        except ValueError: # already a Zope 3 interface
            pass
        assert issubclass(i, zope.interface.Interface)
        normalized_interfaces.append(i)
    return zope.interface.classImplements(class_, *normalized_interfaces)

def classDoesNotImplement(class_, *interfaces):
    # convert any Zope 2 interfaces to Zope 3 using fromZ2Interface
    interfaces = flatten(interfaces)
    normalized_interfaces = []
    for i in interfaces:
        try:
            i = fromZ2Interface(i)
        except ValueError: # already a Zope 3 interface
            pass
        assert issubclass(i, zope.interface.Interface)
        normalized_interfaces.append(i)
    implemented = implementedBy(class_)
    for iface in normalized_interfaces:
        implemented = implemented - iface
    return zope.interface.classImplementsOnly(class_, implemented)
