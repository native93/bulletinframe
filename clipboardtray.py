# Copyright (C) 2007, One Laptop Per Child
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

import logging
import os

from gi.repository import Gtk
from gi.repository import Gdk

from sugar3.graphics import tray
from sugar3.graphics import style
from jarabe.model.shell import ShellModel, get_model
from sugar3.presence import presenceservice

from jarabe.frame import clipboard
from jarabe.frame.clipboardicon import ClipboardIcon
from jarabe.frame import friendstray
from jarabe.model import neighborhood
from jarabe.model import filetransfer
from jarabe.model.buddy import get_owner_instance
from sugar3 import mime


class _ContextMap(object):
    """Maps a drag context to the clipboard object involved in the dragging."""
    def __init__(self):
        self._context_map = {}

    def add_context(self, context, object_id, data_types):
        """Establishes the mapping. data_types will serve us for reference-
        counting this mapping.
        """
        self._context_map[context] = [object_id, data_types]

    def get_object_id(self, context):
        """Retrieves the object_id associated with context.
        Will release the association when this function was called as many
        times as the number of data_types that this clipboard object contains.
        """
        [object_id, data_types_left] = self._context_map[context]

        data_types_left = data_types_left - 1
        if data_types_left == 0:
            del self._context_map[context]
        else:
            self._context_map[context] = [object_id, data_types_left]

        return object_id

    def has_context(self, context):
        return context in self._context_map


class ClipboardTray(tray.VTray):

    MAX_ITEMS = Gdk.Screen.height() / style.GRID_CELL_SIZE - 2

    def __init__(self):
        tray.VTray.__init__(self, align=tray.ALIGN_TO_END)
        self._icons = {}
        self._context_map = _ContextMap()

        cb_service = clipboard.get_instance()
        cb_service.connect('object-added', self._object_added_cb)
        cb_service.connect('object-deleted', self._object_deleted_cb)

    def owns_clipboard(self):
        for icon in self._icons.values():
            if icon.owns_clipboard:
                return True
        return False

    def _add_selection(self, object_id, selection):
        if not selection.get_data():
            return

        selection_data = selection.get_data()

        selection_type_atom = selection.get_data_type()
        selection_type = selection_type_atom.name()

        logging.debug('ClipboardTray: adding type %r', selection_type)

        cb_service = clipboard.get_instance()
        if selection_type == 'text/uri-list':
            uris = selection.get_uris()
            if len(uris) > 1:
                raise NotImplementedError('Multiple uris in text/uri-list'
                                          ' still not supported.')
            file_name = uris[0]
            buddies = neighborhood.get_model().get_buddies()
            mime_type = mime.get_for_file(file_name)
            title = os.path.basename(file_name)
            dialog = Gtk.MessageDialog(None, Gtk.DialogFlags.MODAL, Gtk.MessageType.INFO, Gtk.ButtonsType.YES_NO, "Do you want to start file transfer ?")
            response = dialog.run()
            dialog.destroy()
            if response == Gtk.ResponseType.YES:
                for buddy in buddies:
                    if buddy != get_owner_instance():
                        filetransfer.start_transfer(buddy, file_name, title, "dummy", mime_type)
            cb_service.add_object_format(object_id,
                                         selection_type,
                                         uris[0],
                                         on_disk=True)
        else:
            cb_service.add_object_format(object_id,
                                         selection_type,
                                         selection_data,
                                         on_disk=False)

    def _object_added_cb(self, cb_service, cb_object):
        """ Code snippet to tag clipboard objects from shared activities """
        shell = get_model()
        logging.debug(shell.get_active_activity())
        current = shell.get_active_activity()
        active_id = current.get_activity_id()
        logging.debug(active_id)
        pservice = presenceservice.get_instance()
        instance = pservice.get_activity(active_id, warn_if_none=False)

        """ For a shared activity should have a pservice entry """
        if instance is None:
            return
        logging.debug("cbobject path " + str(cb_object.get_id()))
        if self._icons:
            group = self._icons.values()[0]
        else:
            group = None

        icon = ClipboardIcon(cb_object, group)
        self.add_item(icon)
        icon.show()
        self._icons[cb_object.get_id()] = icon

        objects_to_delete = self.get_children()[:-self.MAX_ITEMS]
        for icon in objects_to_delete:
            logging.debug('ClipboardTray: deleting surplus object')
            cb_service = clipboard.get_instance()
            cb_service.delete_object(icon.get_object_id())

        logging.debug('ClipboardTray: %r was added', cb_object.get_id())

    def _object_deleted_cb(self, cb_service, object_id):
        icon = self._icons[object_id]
        self.remove_item(icon)
        del self._icons[object_id]
        # select the last available icon
        if self._icons:
            last_icon = self.get_children()[-1]
            last_icon.props.active = True

        logging.debug('ClipboardTray: %r was deleted', object_id)

    def drag_motion_cb(self, widget, context, x, y, time):
        logging.debug('ClipboardTray._drag_motion_cb')

        if self._internal_drag(context):
            Gdk.drag_status(context, Gdk.DragAction.MOVE, time)
        else:
            Gdk.drag_status(context, Gdk.DragAction.COPY, time)
            self.props.drag_active = True

        return True

    def drag_leave_cb(self, widget, context, time):
        self.props.drag_active = False

    def drag_drop_cb(self, widget, context, x, y, time):
        logging.debug('ClipboardTray._drag_drop_cb')

        if self._internal_drag(context):
            # TODO: We should move the object within the clipboard here
            if not self._context_map.has_context(context):
                Gdk.drop_finish(context, False, Gtk.get_current_event_time())
            return False

        cb_service = clipboard.get_instance()
        object_id = cb_service.add_object(name="")

        context_targets = context.list_targets()
        self._context_map.add_context(context, object_id, len(context_targets))

        for target in context_targets:
            if str(target) not in ('TIMESTAMP', 'TARGETS', 'MULTIPLE'):
                widget.drag_get_data(context, target, time)

        cb_service.set_object_percent(object_id, percent=100)

        return True

    def drag_data_received_cb(self, widget, context, x, y, selection,
                              targetType, time):
        #logging.debug("pehal " + str(selection.get_uris()[0]))
        logging.debug('ClipboardTray: got data for target %r',
                      selection.get_target())

        object_id = self._context_map.get_object_id(context)
        try:
            if selection is None:
                logging.warn('ClipboardTray: empty selection for target %s',
                             selection.get_target())
            else:
                self._add_selection(object_id, selection)

        finally:
            # If it's the last target to be processed, finish
            # the dnd transaction
            if not self._context_map.has_context(context):
                Gdk.drop_finish(context, True, Gtk.get_current_event_time())

    def _internal_drag(self, context):
        source_widget = Gtk.drag_get_source_widget(context)
        if source_widget is None:
            return False
        view_ancestor = source_widget.get_ancestor(Gtk.Viewport)
        if view_ancestor is self._viewport:
            return True
        else:
            return False
