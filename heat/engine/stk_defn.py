#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import six

from heat.common import exception
from heat.engine import attributes
from heat.engine import status


class StackDefinition(object):
    """Class representing the definition of a Stack, but not its current state.

    This is the interface through which template functions will access data
    about the stack definition, including the template and current values of
    resource reference IDs and attributes.

    This API can be considered stable by third-party Template or Function
    plugins, and no part of it should be changed or removed without an
    appropriate deprecation process.
    """

    def __init__(self, context, template, stack_identifier, resource_data,
                 parent_info=None):
        self._context = context
        self._template = template
        self._resource_data = {} if resource_data is None else resource_data
        self._parent_info = parent_info
        self._zones = None
        self.parameters = template.parameters(stack_identifier,
                                              template.env.params,
                                              template.env.param_defaults)
        self._resource_defns = None
        self._resources = {}

    def clone_with_new_template(self, new_template, stack_identifier,
                                clear_resource_data=False):
        """Create a new StackDefinition with a different template."""
        res_data = {} if clear_resource_data else dict(self._resource_data)
        return type(self)(self._context, new_template, stack_identifier,
                          res_data, self._parent_info)

    @property
    def t(self):
        """The stack's template."""
        return self._template

    @property
    def env(self):
        """The stack's environment."""
        return self._template.env

    def _load_rsrc_defns(self):
        self._resource_defns = self._template.resource_definitions(self)

    def resource_definition(self, resource_name):
        """Return the definition of the given resource."""
        if self._resource_defns is None:
            self._load_rsrc_defns()
        return self._resource_defns[resource_name]

    def enabled_rsrc_names(self):
        """Return the set of names of all enabled resources in the template."""
        if self._resource_defns is None:
            self._load_rsrc_defns()
        return set(self._resource_defns)

    def all_rsrc_names(self):
        """Return the set of names of all resources in the template.

        This includes resources that are disabled due to false conditionals.
        """
        if hasattr(self._template, 'RESOURCES'):
            return set(self._template.get(self._template.RESOURCES,
                                          self._resource_defns or []))
        else:
            return self.enabled_rsrc_names()

    def get_availability_zones(self):
        """Return the list of Nova availability zones."""
        if self._zones is None:
            nova = self._context.clients.client('nova')
            zones = nova.availability_zones.list(detailed=False)
            self._zones = [zone.zoneName for zone in zones]
        return self._zones

    def __contains__(self, resource_name):
        """Return True if the given resource name is present and enabled."""
        if self._resource_defns is not None:
            return resource_name in self._resource_defns
        else:
            # In Cfn templates, we need to know whether Ref refers to a
            # resource or a parameter in order to parse the resource
            # definitions
            return resource_name in self._template[self._template.RESOURCES]

    def __getitem__(self, resource_name):
        """Return a proxy for the given resource."""
        if resource_name not in self._resources:
            res_proxy = ResourceProxy(resource_name,
                                      self.resource_definition(resource_name),
                                      self._resource_data.get(resource_name))
            self._resources[resource_name] = res_proxy
        return self._resources[resource_name]

    @property
    def parent_resource(self):
        """Return a proxy for the parent resource.

        Returns None if the stack is not a provider stack for a
        TemplateResource.
        """
        return self._parent_info


class ResourceProxy(status.ResourceStatus):
    """A lightweight API for essential data about a resource.

    This is the interface through which template functions will access data
    about particular resources in the stack definition, such as the resource
    definition and current values of reference IDs and attributes.

    Resource proxies for some or all resources in the stack will potentially be
    loaded for every check resource operation, so it is essential that this API
    is implemented efficiently, using only the data received over RPC and
    without reference to the resource data stored in the database.

    This API can be considered stable by third-party Template or Function
    plugins, and no part of it should be changed or removed without an
    appropriate deprecation process.
    """

    __slots__ = ('name', '_definition', '_resource_data')

    def __init__(self, name, definition, resource_data):
        self.name = name
        self._definition = definition
        self._resource_data = resource_data

    @property
    def t(self):
        """The resource definition."""
        return self._definition

    def _res_data(self):
        assert self._resource_data is not None, "Resource data not available"
        return self._resource_data

    @property
    def attributes_schema(self):
        """A set of the valid top-level attribute names.

        This is provided for backwards-compatibility for functions that require
        a container with all of the valid attribute names in order to validate
        the template. Other operations on it are invalid because we don't
        actually have access to the attributes schema here; hence we return a
        set instead of a dict.
        """
        return set(self._res_data().attribute_names())

    @property
    def external_id(self):
        """The external ID of the resource."""
        return self._definition.external_id()

    @property
    def state(self):
        """The current state (action, status) of the resource."""
        return self.action, self.status

    @property
    def action(self):
        """The current action of the resource."""
        if self._resource_data is None:
            return self.INIT
        return self._resource_data.action

    @property
    def status(self):
        """The current status of the resource."""
        if self._resource_data is None:
            return self.COMPLETE
        return self._resource_data.status

    def FnGetRefId(self):
        """For the intrinsic function get_resource."""
        if self._resource_data is None:
            return self.name
        return self._resource_data.reference_id()

    def FnGetAtt(self, attr, *path):
        """For the intrinsic function get_attr."""
        if path:
            attr = (attr,) + path
        try:
            return self._res_data().attribute(attr)
        except KeyError:
            raise exception.InvalidTemplateAttribute(resource=self.name,
                                                     key=attr)

    def FnGetAtts(self):
        """For the intrinsic function get_attr when getting all attributes.

        :returns: a dict of all of the resource's attribute values, excluding
                  the "show" attribute.
        """
        all_attrs = self._res_data().attributes()
        return dict((k, v) for k, v in six.iteritems(all_attrs)
                    if k != attributes.SHOW_ATTR)
