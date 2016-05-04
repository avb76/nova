# -*- encoding: utf-8 -*-
'''
Hubble Nova plugin for auditing packages.

Supports both blacklisting and "whitelisting" pacakges. Blacklisted packages
must not be installed. Whitelisted packages must be installed, with options for
requiring a specific version or a minimum or maximum version.

:maintainer: HubbleStack
:maturity: 20160325
:platform: All
:requires: SaltStack

This audit module requires yaml data to execute. It will search the local
directory for any .yaml files, and if it finds a top-level 'pkg' key, it will
use that data.

Sample YAML data, with inline comments:


pkg:
  # Must not be installed
  blacklist:
    # Unique ID for this set of audits
    telnet:
      data:
        # 'osfinger' grain, for multiplatform support
        CentOS Linux-6:
          # pkg name : tag
          - 'telnet': 'CIS-2.1.1'
        # Catch-all, if no osfinger match was found
        '*':
          # pkg name : tag
          - 'telnet': 'telnet-bad'
      # description/alert/trigger are currently ignored, but may be used in the future
      description: 'Telnet is evil'
      alert: email
      trigger: state
  # Must be installed, no version checking (yet)
  whitelist:
    rsh:
      data:
        CentOS Linux-6:
          # Use dict format to define specific version
          - 'rsh':
              tag: 'CIS-2.1.3'
              version: '4.3.2'
          # Dict format can also define ranges (only >= and <= supported)
          - 'rsh-client':
              tag: 'CIS-2.1.3'
              version: '>=4.3.2'
          # String format says "package must be installed, at any version"
          - 'rsh-server': 'CIS-2.1.4'
        CentOS Linux-7:
          - 'rsh': 'CIS-2.1.3'
          - 'rsh-server': 'CIS-2.1.4'
        '*':
          - 'rsh-client': 'CIS-5.1.2'
          - 'rsh-redone-client': 'CIS-5.1.2'
          - 'rsh-server': 'CIS-5.1.3'
          - 'rsh-redone-server': 'CIS-5.1.3'
      description: 'RSH is awesome'
      alert: email
      trigger: state

'''
from __future__ import absolute_import
import logging

import fnmatch
import yaml
import os
import copy
import salt.utils

from distutils.version import LooseVersion

log = logging.getLogger(__name__)


def __virtual__():
    if salt.utils.is_windows():
        return False, 'This audit module only runs on linux'
    return True


def audit(data_list, tags, verbose=False):
    '''
    Run the pkg audits contained in the YAML files processed by __virtual__
    '''
    __data__ = {}
    for data in data_list:
        _merge_yaml(__data__, data)
    __tags__ = _get_tags(__data__)

    log.trace('pkg audit __data__:')
    log.trace(__data__)
    log.trace('pkg audit __tags__:')
    log.trace(__tags__)

    ret = {'Success': [], 'Failure': [], 'Controlled': []}
    for tag in __tags__:
        if fnmatch.fnmatch(tag, tags):
            for tag_data in __tags__[tag]:
                if 'control' in tag_data:
                    ret['Controlled'].append(tag_data)
                    continue
                name = tag_data['name']
                audittype = tag_data['type']

                # Blacklisted packages (must not be installed)
                if audittype == 'blacklist':
                    if __salt__['pkg.version'](name):
                        ret['Failure'].append(tag_data)
                    else:
                        ret['Success'].append(tag_data)

                # Whitelisted packages (must be installed)
                elif audittype == 'whitelist':
                    if 'version' in tag_data:
                        mod, _, version = tag_data['version'].partition('=')
                        if not version:
                            version = mod
                            mod = ''

                        if mod == '<':
                            if (LooseVersion(__salt__['pkg.version'](name)) <=
                                    LooseVersion(version)):
                                ret['Success'].append(tag_data)
                            else:
                                ret['Failure'].append(tag_data)

                        elif mod == '>':
                            if (LooseVersion(__salt__['pkg.version'](name)) >=
                                    LooseVersion(version)):
                                ret['Success'].append(tag_data)
                            else:
                                ret['Failure'].append(tag_data)

                        elif not mod:
                            # Just peg to the version, no > or <
                            if __salt__['pkg.version'](name) == version:
                                ret['Success'].append(tag_data)
                            else:
                                ret['Failure'].append(tag_data)

                        else:
                            # Invalid modifier
                            log.error('Invalid modifier in version {0} for pkg {1} audit {2}'
                                      .format(tag_data['version'], name, tag))
                            tag_data = copy.deepcopy(tag_data)
                            # Include an error in the failure
                            tag_data['error'] = 'Invalid modifier {0}'.format(mod)
                            ret['Failure'].append(tag_data)

                    else:  # No version checking
                        if __salt__['pkg.version'](name):
                            ret['Success'].append(tag_data)
                        else:
                            ret['Failure'].append(tag_data)

    if not verbose:
        failure = []
        success = []
        controlled = []

        tags_descriptions = set()

        for tag_data in ret['Failure']:
            tag = tag_data['tag']
            description = tag_data.get('description')
            if (tag, description) not in tags_descriptions:
                failure.append({tag: description})
                tags_descriptions.add((tag, description))

        tags_descriptions = set()

        for tag_data in ret['Success']:
            tag = tag_data['tag']
            description = tag_data.get('description')
            if (tag, description) not in tags_descriptions:
                success.append({tag: description})
                tags_descriptions.add((tag, description))

        control_reasons = set()

        for tag_data in ret['Controlled']:
            tag = tag_data['tag']
            control_reason = tag_data.get('control', '')
            description = tag_data.get('description')
            if (tag, description, control_reason) not in control_reasons:
                tag_dict = {'description': description,
                        'control': control_reason}
                controlled.append({tag: tag_dict})
                control_reasons.add((tag, description, control_reason))

        ret['Controlled'] = controlled
        ret['Success'] = success
        ret['Failure'] = failure

    if not ret['Controlled']:
        ret.pop('Controlled')

    return ret


def _merge_yaml(ret, data):
    '''
    Merge two yaml dicts together at the pkg:blacklist and pkg:whitelist level
    '''
    if 'pkg' not in ret:
        ret['pkg'] = {}
    for topkey in ('blacklist', 'whitelist'):
        if topkey in data.get('pkg', {}):
            if topkey not in ret['pkg']:
                ret['pkg'][topkey] = []
            for key, val in data['pkg'][topkey].iteritems():
                ret['pkg'][topkey].append({key: val})
    return ret


def _get_tags(data):
    '''
    Retrieve all the tags for this distro from the yaml
    '''
    ret = {}
    distro = __grains__.get('osfinger')
    for toplist, toplevel in data.get('pkg', {}).iteritems():
        # pkg:blacklist
        for audit_dict in toplevel:
            # pkg:blacklist:0
            for audit_id, audit_data in audit_dict.iteritems():
                # pkg:blacklist:0:telnet
                tags_dict = audit_data.get('data', {})
                # pkg:blacklist:0:telnet:data
                tags = tags_dict.get(distro, tags_dict.get('*', []))
                # pkg:blacklist:0:telnet:data:Debian-8
                if isinstance(tags, dict):
                    # malformed yaml, convert to list of dicts
                    tmp = []
                    for name, tag in tags.iteritems():
                        tmp.append({name: tag})
                    tags = tmp
                for item in tags:
                    for name, tag in item.iteritems():
                        tag_data = {}
                        # Whitelist could have a dictionary, not a string
                        if isinstance(tag, dict):
                            tag_data = copy.deepcopy(tag)
                            tag = tag_data.pop('tag')
                        if tag not in ret:
                            ret[tag] = []
                        formatted_data = {'name': name,
                                          'tag': tag,
                                          'module': 'pkg',
                                          'type': toplist}
                        formatted_data.update(tag_data)
                        formatted_data.update(audit_data)
                        formatted_data.pop('data')
                        ret[tag].append(formatted_data)
    return ret
