import collections


class APIObject(object):
    def __init__(self, **kwargs):
        for k, v in kwargs.iteritems():
            self.__setattr__(k, v)

    def __repr__(self):
        return str(self.__dict__)


class User(APIObject):
    def __init__(self, **kwargs):
        super(User, self).__init__(**kwargs)

    @property
    def is_staff(self):
        return 'staff' in self.roles


class Order(APIObject):
    def __init__(self, **kwargs):
        super(Order, self).__init__(**kwargs)


class Scene(APIObject):
    def __init__(self, **kwargs):
        super(Scene, self).__init__(**kwargs)


def deep_update(source, overrides):
    """
    update a nested dictionary. modify source in place
    credit: stackoverflow user charlax
    http://stackoverflow.com/questions/3232943
    :param source: dictionary to update
    :param overrides: values to override
    :return: updated source
    """
    for key, value in overrides.iteritems():
        if isinstance(value, collections.Mapping) and value:
            returned = deep_update(source.get(key, {}), value)
            source[key] = returned
        else:
            source[key] = overrides[key]
    return source


def is_num(value):
    try:
        # is it an int
        rv = int(value)
    except:
        try:
            # is it a float
            rv = float(value)
        except:
            # then return a str
            rv = value
    return rv


def gen_nested_dict(inlist, val):
    inlist.reverse()
    for idx, item in enumerate(inlist):
        _indict = {item: val} if idx is 0 else {item: _indict}
    return _indict


def format_messages(inmessages):
    outlist = []
    if isinstance(inmessages, dict):
        for key in inmessages:
            outlist.append(key+"<br/>")
            if isinstance(inmessages[key], basestring):
                outlist.append(inmessages[key])
            else:
                for item in inmessages[key]:
                    outlist.append("&#8594;"+item+"<br/>")
        return "".join(outlist)
    elif isinstance(inmessages, list):
        if all((isinstance(m, basestring) for m in inmessages)):
            return "<br/>".join(inmessages)
        else:
            return "<br/>".join(format_messages(m) for m in inmessages)
    else:
        return inmessages

conversions = {
    'products': {
         'l1': 'original input products',
         'swe': 'dynamic surface water extent',
         'sr_evi': 'sr_evi',
         'cloud': 'cfmask',
         'pixel_qa': 'l2 pixel qa',
         'sr_savi': 'sr_savi',
         'sr_nbr2': 'sr_nbr2',
         'sr_nbr': 'sr_nbr',
         'sr_ndmi': 'sr_ndmi',
         'sr': 'surface reflectance',
         'sr_msavi': 'sr_msavi',
         'lst': 'land surface temperature',
         'source_metadata': 'original input metadata',
         'bt': 'brightness temperature',
         'toa': 'top of atmosphere',
         'stats': 'plots and statistics',
         'sr_ndvi': 'sr_ndvi',
         'toa_ndvi': 'toa_ndvi'
    }
}
