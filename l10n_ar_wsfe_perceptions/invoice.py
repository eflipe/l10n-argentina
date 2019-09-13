# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (c) 2013 E-MIPS (http://www.e-mips.com.ar) All Rights Reserved.
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from osv import osv

# TODO: Hardcodeamos el codigo porque es por a nivel Jurisdiccional
# Despues podemos pensar algo mejor
codes = {'nacional': '1', 'provincial': '2', 'municipal': '3'}


class account_invoice(osv.osv):
    _name = "account.invoice"
    _inherit = "account.invoice"

    def hook_add_taxes(self, cr, uid, inv, detalle):
        detalle = super(account_invoice, self).hook_add_taxes(cr, uid, inv, detalle)
        perc_array = []

        for perception in inv.perception_ids:
            code = codes[perception.perception_id.jurisdiccion]
            perc = {'Id': code, 'Desc': perception.name[:80], 'BaseImp': abs(perception.base_currency), 'Importe': abs(perception.tax_currency), 'Alic': 0.0}
            perc_array.append(perc)

        if detalle['Tributos']:
            detalle['Tributos'] += perc_array
        else:
            detalle['Tributos'] = perc_array

        return detalle

account_invoice()
