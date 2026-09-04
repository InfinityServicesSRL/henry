# Rollback de los cambios de configuracion del 31 ago 2026

Cambios aplicados en PRODUCCION, compania STI (id 1), via MCP.
Ninguno tiene efecto contable: cambian el comportamiento futuro, no asientos.

## 1. purchase_method: "purchase" -> "receive"  (220 fichas)

ESTADO: PENDIENTE DE IMPORTAR. El usuario del MCP no puede modificar
articulos -- module_agsupply limita crear/modificar productos a Solanyi,
Angelica, Martin, Henry y Maria. Se entrega como CSV para importar:
1_purchase_method_recibidas.csv

Que hace: la factura del proveedor se propone por lo RECIBIDO, no por lo
pedido. Es lo que activa la conciliacion a tres bandas y detiene la
acumulacion en 21021200 Bienes Recibidos no Facturados.

Alcance: product.template con purchase_ok = True e is_storable = True.
Son 220 de 233 fichas almacenables comprables; 13 ya estaban correctas.
Los comprables NO almacenables quedan fuera A PROPOSITO: para un gasto,
facturar por lo pedido es correcto y no hay recepcion contra la cual casar.

### Para revertir

```python
ids = [4134,4228,4227,4167,4126,4148,3981,3568,3569,4026,3571,3572,4037,4154,4041,4028,4040,4022,3573,4036,3924,4163,3909,3927,4077,3939,3915,4136,3803,3575,4294,3576,3579,3829,3940,3543,4142,3814,3790,3804,4322,4243,3594,4303,3545,4308,4310,4242,3602,3805,3815,3807,3830,3791,3819,3990,3991,4144,3603,3604,3992,4277,4213,3605,3607,3609,3610,3993,3994,3611,3945,3614,3943,3617,3619,3933,3865,3624,3625,3627,3628,4079,3631,3632,4023,3633,3635,3636,3637,3639,3640,3642,3876,3644,4145,3999,4000,4001,3648,3650,3651,3652,3881,3653,3655,4160,4246,3657,3659,3660,3662,3837,4178,4043,4066,4278,4220,3664,4304,4305,4312,4307,3675,3678,4237,4293,4147,4250,3834,4003,4118,4004,4006,4007,4008,4009,3704,4027,4042,4029,4033,4044,4076,3706,3922,4230,4128,4119,4121,4120,4209,4011,4012,3861,4030,3866,3942,3816,3938,3785,4251,4034,3875,3712,3713,3714,3715,4015,3921,3718,3566,4285,3720,4130,4131,3871,3908,4153,3846,3914,3962,3937,3926,3732,3737,3869,3911,4180,4311,4017,3794,4197,3808,3897,4018,3806,3831,3792,3820,3802,3904,4328,4319,3796,4266,4138,3853,3918,3839,3878,3954,4219,3901,3893,3774,4211,4261,4125,4184,4236]
env["product.template"].browse(ids).write({"purchase_method": "purchase"})
env.cr.commit()
```

## 2. invoice_policy: "order" -> "delivery"  (36 fichas)

ESTADO: PENDIENTE DE IMPORTAR. Archivo: 2_invoice_policy_entregadas.csv

Que hace: la factura de venta se propone por lo ENTREGADO, no por lo pedido.
Evita facturar mercancia que sigue en el almacen.

Alcance: product.template con sale_ok = True e is_storable = True.

### Para revertir

```python
ids = [4167,3939,3610,3993,3617,3619,3951,3933,3625,3627,3628,4079,3631,3635,3636,3637,3639,3642,3876,3644,3648,3655,4246,3837,4118,4004,4006,4007,3703,4008,4033,4119,4121,4120,4209,4011]
env["product.template"].browse(ids).write({"invoice_policy": "order"})
env.cr.commit()
```

## 3. account.account 11010202 Cuenta transitoria: reconcile False -> True

ESTADO: APLICADO Y VERIFICADO el 31 ago 2026 via MCP.

Que hace: permite que las partidas de esa cuenta se crucen entre si. Sin
esto el circuito no esta atascado: no tiene forma de cerrarse.

### Para revertir

```python
env["account.account"].search([("code","=","11010202")]).write({"reconcile": False})
env.cr.commit()
```

## Lo que NO se toco, y por que

- Las 5 categorias en inventario periodico: tiene efecto contable y espera al
  auditor externo (fecha de corte y tratamiento del ajuste).
- La reclasificacion de 21021200 a pasivo corriente: hundiria la razon
  corriente usando una cifra que en buena parte es sedimento. Primero se
  cierra el circuito, despues se clasifica lo que quede.
- Las cuentas de valoracion 11050100 "NO USAR" en Ofertas, Servicio y
  Expenses: decision contable sobre a que cuenta deben apuntar.
