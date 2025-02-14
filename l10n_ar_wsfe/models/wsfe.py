##############################################################################
#   Copyright (c) 2018 Eynes/E-MIPS (www.eynes.com.ar)
#   License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
##############################################################################

import time
from datetime import datetime

from odoo import _, api, fields, models
from odoo.addons.l10n_ar_wsfe.wsfetools.wsfe_easywsy import WSFE
from odoo.exceptions import UserError


class WsfeOptionals(models.Model):
    _name = "wsfe.optionals"
    _description = "WSFE Optionals"

    code = fields.Char('Code', required=False, size=4)
    name = fields.Char('Desc', required=True, size=64)
    to_date = fields.Date('Effect Until')
    from_date = fields.Date('Effective From')
    from_afip = fields.Boolean('From AFIP')
    wsfe_config_id = fields.Many2one('wsfe.config', 'WSFE Configuration')


class WsfeTaxCodes(models.Model):
    _name = "wsfe.tax.codes"
    _description = "Tax Codes"

    code = fields.Char('Code', required=False, size=4)
    name = fields.Char('Desc', required=True, size=64)
    to_date = fields.Date('Effect Until')
    from_date = fields.Date('Effective From')
    tax_id = fields.Many2one('account.tax', 'Account Tax')
    wsfe_config_id = fields.Many2one('wsfe.config', 'WSFE Configuration')
    from_afip = fields.Boolean('From AFIP')
    exempt_operations = fields.Boolean(
        'Exempt Operations',
        help='Check it if this VAT Tax corresponds to vat tax exempts ' +
        'operations, such as to sell books, milk, etc. The taxes with this ' +
        'checked, will be reported to AFIP as  exempt operations ' +
        '(base amount) without VAT applied on this')


class WsfeConfig(models.Model):
    _name = "wsfe.config"
    _description = "Configuration for WSFE"

    _webservice_class = WSFE

    name = fields.Char('Name', size=64, required=True)
    cuit = fields.Char(related='company_id.partner_id.vat', string='Cuit')
    url = fields.Char('URL for WSFE', size=60, required=True)
    homologation = fields.Boolean(
        'Homologation',
        help="If true, there will be some validations that are disabled, " +
        "for example, invoice number correlativeness")
    point_of_sale_ids = fields.Many2many(
        'pos.ar', 'pos_ar_wsfe_rel', 'wsfe_config_id',
        'pos_ar_id', 'Points of Sale')
    vat_tax_ids = fields.One2many(
        'wsfe.tax.codes', 'wsfe_config_id',
        'Taxes', domain=[('from_afip', '=', True)])
    exempt_operations_tax_ids = fields.One2many(
        'wsfe.tax.codes', 'wsfe_config_id', 'Taxes of Exempt Operations',
        domain=[('from_afip', '=', False), ('exempt_operations', '=', True)])
    wsaa_ticket_id = fields.Many2one('wsaa.ta', 'Ticket Access')
    company_id = fields.Many2one('res.company', 'Company Name', required=True)
    services_date_difference = fields.Integer(
        'Services and Products', default=10)
    products_date_difference = fields.Integer(
        'Products', default=5)
    optional_ids = fields.One2many('wsfe.optionals', 'wsfe_config_id', 'Optionals', domain=[('from_afip', '=', True)])

    _defaults = {
        'company_id': lambda self, cr, uid, context=None:
            self.env['res.users']._get_company(cr, uid, context=context),
        'homologation': lambda *a: False,
    }

    @api.model
    def create(self, vals):

        # Creamos tambien un TA para este servcio y esta compania
        ta_obj = self.env['wsaa.ta']
        wsaa_obj = self.env['wsaa.config']
        service_obj = self.env['afipws.service']

        # Buscamos primero el wsaa que corresponde a esta compania
        # porque hay que recordar que son unicos por compania
        wsaa = wsaa_obj.search([('company_id', '=', vals['company_id'])])
        service = service_obj.search([('name', '=', 'wsfe')])
        if wsaa:
            ta_vals = {
                'name': service.id,
                'company_id': vals['company_id'],
                'config_id': wsaa.id,
            }

            ta = ta_obj.create(ta_vals)
            vals['wsaa_ticket_id'] = ta.id

        return super(WsfeConfig, self).create(vals)

    @api.multi
    def unlink(self):
        for wsfe_conf in self:
            wsfe_conf.wsaa_ticket_id.unlink()
        res = super(WsfeConfig, self).unlink()
        return res

    @api.model
    def get_config(self):
        company_id = self.env.context.get('company_id')
        no_raise = self.env.context.get('without_raise', False)
        if not company_id and not no_raise:
            raise UserError(
                _('Company Error!\n') +
                _('There is no company being used by this user'))

        ids = self.search([('company_id', '=', company_id)])
        if not ids and not no_raise:
            raise UserError(
                _('WSFE Config Error!\n') +
                _('There is no WSFE configuration set to this company'))

        return ids

    @api.model
    def check_errors(self, res, raise_exception=True):
        msg = ''
        if 'errors' in res or 'Errors' in res:
            res_err = res['errors'] if 'errors' in res else res['Errors']
            if isinstance(res_err, list):
                err_array = res_err
                err_var_name = 'msg'
                err_var_code = 'code'
            elif 'Err' in res_err and isinstance(res_err.Err, list):
                err_array = res_err.Err
                err_var_name = 'Msg'
                err_var_code = 'Code'
            errors = [getattr(error, err_var_name) for error in err_array]
            err_codes = [str(getattr(error, err_var_code))
                         for error in err_array]
            msg = ' '.join(errors)
            msg = msg + ' Codigo/s Error:' + ' '.join(err_codes)

            if msg != '' and raise_exception:
                raise UserError(_('WSFE Error!\n') + msg)

        return msg

    @api.model
    def check_observations(self, res):
        msg = ''
        if 'observations' in res:
            observations = [obs.msg for obs in res['observations']]
            obs_codes = [str(obs.code) for obs in res['observations']]
            msg = ' '.join(observations)
            msg = msg + ' Codigo/s Observacion:' + ' '.join(obs_codes)

            # Escribimos en el log del cliente web
            self.log(None, msg)

        return msg

    @api.multi
    def _log_wsfe_request(self, pos, voucher_type_code, details, res):
        self.ensure_one()
        wsfe_req_obj = self.env['wsfe.request']
        voucher_type_obj = self.env['wsfe.voucher_type']
        voucher_type = voucher_type_obj.search(
            [('code', '=', voucher_type_code)])
        voucher_type_name = voucher_type.name
        req_details = []
        for index, comp in enumerate(res['Comprobantes']):
            detail = details[index]

            # Esto es para fixear un bug que al hacer un refund,
            # si fallaba algo con la AFIP
            # se hace el rollback por lo tanto el refund que se estaba
            # creando ya no existe en
            # base de datos y estariamos violando una foreign
            # key contraint. Por eso,
            # chequeamos que existe info de la invoice_id,
            # sino lo seteamos en False
            read_inv = self.env['account.invoice'].browse(detail['invoice_id'])

            if not read_inv:
                invoice_id = False
            else:
                invoice_id = detail['invoice_id']

            det = {
                'name': invoice_id,
                'concept': str(detail['Concepto']),
                'doctype': detail['DocTipo'],  # TODO: Poner aca el nombre del tipo de documento  # noqa
                'docnum': str(detail['DocNro']),
                'voucher_number': comp['CbteHasta'],
                'voucher_date': comp['CbteFch'],
                'amount_total': detail['ImpTotal'],
                'cae': comp['CAE'],
                'cae_duedate': comp['CAEFchVto'],
                'result': comp['Resultado'],
                'currency': detail['MonId'],
                'currency_rate': detail['MonCotiz'],
                'observations': '\n'.join(comp['Observaciones']),
            }

            req_details.append((0, 0, det))

        # Chequeamos el reproceso
        reprocess = False
        if res['Reproceso'] == 'S':
            reprocess = True

        errors = '\n'.join(res['Errores']).encode('latin1').decode('utf8')
        vals = {
            'voucher_type': voucher_type_name,
            'nregs': len(details),
            'pos_ar': '%04d' % pos,
            'date_request': time.strftime('%Y-%m-%d %H:%M:%S'),
            'result': res['Resultado'],
            'reprocess': reprocess,
            'errors': errors,
            'detail_ids': req_details,
        }

        return wsfe_req_obj.create(vals)

    @api.multi
    def ws_auth(self):
        # TODO This block is repeated, should persist only in WSAA, I think
        token, sign = self.wsaa_ticket_id.get_token_sign()
        auth = {
            'Token': token,
            'Sign': sign,
            'Cuit': self.cuit
        }
        ws = self._webservice_class(self.url)
        ws.login('Auth', auth)
        return ws

    @api.multi
    def get_last_voucher(self, pos, voucher_type):
        self.ensure_one()
        ws = self.ws_auth()
        data = {
            'FECompUltimoAutorizado': {
                'CbteTipo': voucher_type,
                'PtoVta': pos,
            }
        }
        ws.add(data)
        response = ws.request('FECompUltimoAutorizado')
        res = ws.parse_response(response)
        del(ws)
        last = res['response'].CbteNro
        return last

    @api.multi
    def get_voucher_info(self, pos, voucher_type, number):
        self.ensure_one()
        ws = self.ws_auth()
        data = {
            'FECompConsultar': {
                'FeCompConsReq': {
                    'CbteTipo': voucher_type,
                    'CbteNro': number,
                    'PtoVta': pos,
                }
            }
        }
        ws.add(data)
        response = ws.request('FECompConsultar')
        if not hasattr(response, 'ResultGet'):
            raise UserError(
                _("Error fetching voucher from AFIP!"))
        return response.ResultGet

    @api.multi
    def read_tax(self):
        self.ensure_one()
        if not self.cuit:
            raise UserError(
                _("Please configure the company VAT before get Taxes!"))
        wsfe_tax_model = self.env['wsfe.tax.codes']
        wsfe_optionals_obj = self.env['wsfe.optionals']

        ws = self.ws_auth()
        data = {
            'FEParamGetTiposIva': {
            }
        }
        ws.add(data, no_check="all")
        response = ws.request('FEParamGetTiposIva')
        err = self.check_errors(response, raise_exception=False)
        if err:
            raise UserError(_("Error reading Taxes!\n") + err)
        for tax in response[0][0]:
            fd = datetime.strptime(tax.FchDesde, '%Y%m%d')
            try:
                td = datetime.strptime(tax.FchHasta, '%Y%m%d')
            except ValueError:
                td = False
            vals = {
                'code': tax.Id,
                'name': tax.Desc,
                'to_date': td,
                'from_date': fd,
                'wsfe_config_id': self.id,
                'from_afip': True,
            }

            # Si no existe el impuesto en la DB, lo creamos de acuerdo a AFIP
            tax = wsfe_tax_model.search([
                ('code', '=', tax.Id),
                ('wsfe_config_id', '=', self.id),
            ])
            if not tax:
                wsfe_tax_model.create(vals)

            # Si los codigos estan en la db los modifico
            else:
                tax.write(vals)

        # now optionals
        ws = self.ws_auth()
        data = {
            'FEParamGetTiposOpcional': {
            }
        }
        ws.add(data, no_check="all")
        response = ws.request('FEParamGetTiposOpcional')
        err = self.check_errors(response, raise_exception=False)
        if err:
            raise UserError(_("Error reading Taxes!\n") + err)
        for r in response[0][0]:
            res_c = wsfe_optionals_obj.search([
                ('code', '=', r.Id),
                ('wsfe_config_id', '=', self.id),
            ])

            #~ Si tengo no los codigos de esos Opcionales en la db, los creo
            if not len(res_c):
                fd = datetime.strptime(r.FchDesde, '%Y%m%d')
                try:
                    td = datetime.strptime(r.FchHasta, '%Y%m%d')
                except ValueError:
                    td = False

                wsfe_optionals_obj.create({
                    'code': r.Id, 'name': r.Desc, 'to_date': td,
                    'from_date': fd, 'wsfe_config_id': self.id, 'from_afip': True})
            #~ Si los codigos estan en la db los modifico
            else:
                fd = datetime.strptime(r.FchDesde, '%Y%m%d')
                #'NULL' ?? viene asi de fe_param_get_tipos_iva():
                try:
                    td = datetime.strptime(r.FchHasta, '%Y%m%d')
                except ValueError:
                    td = False

                res_c.write({
                    'code': r.Id, 'name': r.Desc, 'to_date': td,
                    'from_date': fd, 'wsfe_config_id': self.id, 'from_afip': True})

        return True

    @api.multi
    def prepare_details(self, invoices):
        obj_precision = self.env['decimal.precision']
        invoice_obj = self.env['account.invoice']

        details = []

        first_num = self._context.get('first_num', False)
        cbte_nro = 0

        for inv in invoices:
            detalle = {}

            fiscal_position_id = inv.fiscal_position_id
            doc_type = inv.partner_id.document_type_id and \
                inv.partner_id.document_type_id.afip_code or '99'
            doc_num = inv.partner_id.vat or '0'

            # Chequeamos si el concepto es producto,
            # servicios o productos y servicios
            product_service = [l.product_id and l.product_id.type or
                               'consu' for l in inv.invoice_line_ids]

            service = all([ps == 'service' for ps in product_service])
            products = all([ps == 'consu' or ps == 'product' for
                            ps in product_service])

            # Calculamos el concepto de la factura, dependiendo de las
            # lineas de productos que se estan vendiendo
            concept = None
            if products:
                concept = 1  # Productos
            elif service:
                concept = 2  # Servicios
            else:
                concept = 3  # Productos y Servicios

            if not fiscal_position_id:
                raise UserError(
                    _('Customer Configuration Error\n') +
                    _('There is no fiscal position configured for ' +
                      'the customer %s') % inv.partner_id.name)

            # Obtenemos el numero de comprobante a enviar a la AFIP teniendo en
            # cuenta que inv.number == 000X-00000NN o algo similar.
            if not inv.internal_number:
                if not first_num:
                    raise UserError(
                        _("WSFE Error!\n") +
                        _("There is no first invoice number declared!"))
                inv_number = first_num
            else:
                inv_number = inv.internal_number

            if not cbte_nro:
                cbte_nro = inv_number.split('-')[1]
                cbte_nro = int(cbte_nro)
            else:
                cbte_nro = cbte_nro + 1

            date_invoice = datetime.strptime(inv.date_invoice, '%Y-%m-%d')
            formatted_date_invoice = date_invoice.strftime('%Y%m%d')
            date_due = inv.date_due and datetime.strptime(
                inv.date_due, '%Y-%m-%d').strftime('%Y%m%d') or \
                formatted_date_invoice

            # company_currency_id = company.currency_id.id
            # if inv.currency_id.id != company_currency_id:
            #     raise UserError(
            #         _("WSFE Error!"),
            #         _("Currency cannot be different to company currency. " +
            #           "Also check that company currency is ARS"))

            detalle['invoice_id'] = inv.id

            detalle['Concepto'] = concept
            detalle['DocTipo'] = doc_type
            detalle['DocNro'] = doc_num
            detalle['CbteDesde'] = cbte_nro
            detalle['CbteHasta'] = cbte_nro
            detalle['CbteFch'] = date_invoice.strftime('%Y%m%d')
            if concept in [2, 3]:
                detalle['FchServDesde'] = formatted_date_invoice
                detalle['FchServHasta'] = formatted_date_invoice
                detalle['FchVtoPago'] = date_due

            # Obtenemos la moneda de la factura
            # Lo hacemos por el wsfex_config, por cualquiera de ellos
            # si es que hay mas de uno
            currency_code_obj = self.env['wsfex.currency.codes']
            currency_code_ids = currency_code_obj.search(
                [('currency_id', '=', inv.currency_id.id)])

            if not currency_code_ids:
                raise UserError(
                    _("WSFE Error!\n") +
                    _("Currency has to be configured correctly " +
                      "in WSFEX Configuration."))

            currency_code = currency_code_ids[0].code

            # Cotizacion
            company_id = self.env.user.company_id
            company_currency_id = company_id.currency_id
            invoice_rate = 1.0
            if inv.currency_id.id != company_currency_id.id:
                invoice_rate = inv.currency_rate

            detalle['MonId'] = currency_code
            detalle['MonCotiz'] = invoice_rate

            iva_array = []

            importe_neto = 0.0
            importe_operaciones_exentas = inv.amount_exempt
            importe_iva = 0.0
            importe_tributos = 0.0
            importe_total = 0.0
            importe_neto_no_gravado = inv.amount_no_taxed

            # Procesamos las taxes
            taxes = inv.tax_line_ids
            for tax in taxes:
                found = False
                for eitax in self.vat_tax_ids + self.exempt_operations_tax_ids:
                    if eitax.tax_id.id == tax.id:
                        found = True
                        if eitax.exempt_operations:
                            pass
                            # importe_operaciones_exentas += tax.base
                        else:
                            importe_iva += tax.amount
                            importe_neto += tax.base
                            iva2 = {
                                'Id': int(eitax.code),
                                'BaseImp': tax.base,
                                'Importe': tax.amount
                            }
                            iva_array.append(iva2)
                if not found:
                    importe_tributos += tax.amount

            importe_total = importe_neto + importe_neto_no_gravado + \
                importe_operaciones_exentas + importe_iva + importe_tributos
            print('Importe total: ', importe_total)
            print('Importe neto gravado: ', importe_neto)
            print('Importe IVA: ', importe_iva)
            print('Importe Operaciones Exentas: ', importe_operaciones_exentas)
            print('Importe neto No gravado: ', importe_neto_no_gravado)
            print('Array de IVA: ', iva_array)

            # Chequeamos que el Total calculado por el Open, se corresponda
            # con el total calculado por nosotros, tal vez puede haber un error
            # de redondeo
            prec = obj_precision.precision_get('Account')
            if round(importe_total, prec) != round(inv.amount_total, prec):
                raise UserError(
                    _('Error in amount_total!\n') +
                    _("The total amount of the invoice does not " +
                      "corresponds to the total calculated.\n" +
                      "Maybe there is an rounding error!. " +
                      "(Amount Calculated: %f)") % (importe_total))

            # Detalle del array de IVA
            detalle['Iva'] = iva_array

            # Detalle de los importes
            detalle['ImpOpEx'] = importe_operaciones_exentas
            detalle['ImpNeto'] = importe_neto
            detalle['ImpTotConc'] = importe_neto_no_gravado
            detalle['ImpIVA'] = importe_iva
            detalle['ImpTotal'] = inv.amount_total
            detalle['ImpTrib'] = importe_tributos
            detalle['Tributos'] = None
            # print('Detalle de facturacion: ', detalle)

            # Agregamos un hook para agregar tributos o IVA que pueda ser
            # llamado de otros modulos. O mismo para modificar el detalle.
            detalle = invoice_obj.hook_add_taxes(inv, detalle)

            details.append(detalle)

        # print('Detalles: ', details)
        return details


class WsfeVoucherType(models.Model):
    """
    Es un comprobante que una empresa envía a su cliente, en la que se
    le notifica haber cargado o debitado en su cuenta una determinada suma
    o valor, por el concepto que se indica en la misma nota.
    Este documento incrementa el valor de la deuda o saldo de la cuenta,
    ya sea por un error en la facturación, interés por mora en el pago,
    o cualquier otra circunstancia que signifique el incremento
    del saldo de una cuenta.

    It is a proof that a company sends to your client, which is notified
    to be charged or debited the account a certain sum or value,
    the concept shown in the same note.
    This document increases the value of the debt or account balance,
    either by an error in billing, interest for late payment,
    or any other circumstance that means the increase in the balance
    of an account.
    """
    _name = "wsfe.voucher_type"
    _description = "Voucher Type for Electronic Invoice"

    name = fields.Char(
        'Name', size=64, required=True, readonly=False,
        help='Voucher Type, eg.: Factura A, Nota de Credito B, etc.')
    code = fields.Char(
        'Code', size=4, required=True,
        help='Internal Code assigned by AFIP for voucher type')
    voucher_model = fields.Selection([
        ('invoice', 'Factura/NC/ND'),
        ('voucher', 'Recibo'), ], 'Voucher Model', index=True, required=True)
    document_type = fields.Selection([
        ('out_invoice', 'Factura'),
        ('out_refund', 'Nota de Credito'),
        ('out_debit', 'Nota de Debito'),
    ], 'Document Type', index=True, required=True, readonly=False)
    denomination_id = fields.Many2one('invoice.denomination',
                                      'Denomination', required=False)
    fiscal_type_id = fields.Many2one('account.invoice.fiscal.type', 'Fiscal type')

    @api.model
    def get_voucher_type(self, voucher):
        voucher.ensure_one()
        # Chequeamos el modelo
        voucher_model = None
        model = voucher._table

        if model == 'account_invoice':
            voucher_model = 'invoice'

            denomination_id = voucher.denomination_id.id
            type = voucher.type
            fiscal_type_id = voucher.fiscal_type_id.id
            if type.startswith("in"):
                type = "out_%s" % type[3:]
            if type == 'out_invoice':
                if voucher.is_debit_note:
                    type = 'out_debit'

            res = self.search([
                ('voucher_model', '=', voucher_model),
                ('document_type', '=', type),
                ('denomination_id', '=', denomination_id)
            ])

            if fiscal_type_id:
                res = res.filtered(lambda x: x.fiscal_type_id.id == fiscal_type_id)

            if not len(res):
                raise UserError(
                    _("Voucher type error!\n") +
                    _("There is no voucher type that corresponds " +
                      "to this object"))

            if len(res) > 1:
                raise UserError(
                    _("Voucher type error!\n") +
                    _("There is more than one voucher type that " +
                      "corresponds to this object"))

            return res.code

        elif model == 'account_voucher':
            voucher_model = 'voucher'

        return None
