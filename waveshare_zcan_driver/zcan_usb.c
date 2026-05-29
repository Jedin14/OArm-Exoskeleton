// SPDX-License-Identifier: GPL-2.0
/*
 * zcan_usb - Linux SocketCAN Driver for NXP USB CANFD DEBUG
 *
 * Supports: NXP USB CANFD DEBUG / Zhiyuan Electronics USBCANFD-200U
 * USB ID:   04d8:0053
 * Version:  0.5.3
 *
 * This driver was completely reverse-engineered from USB traffic captures
 * and static analysis of the vendor library (libcontrolcanfd.a).
 * No official protocol documentation was available.
 *
 * Key findings:
 *  - Commands use BEEF/DEAD framing with checksum
 *  - AES-128 ECB challenge-response authentication on open
 *  - TX frames use 0xF1 (classic CAN) or 0xF2 (CANFD) marker
 *  - TX payload is 26 bytes for classic CAN, BEEF-wrapped
 *  - CH0 TX goes on EP1 OUT (same as commands); CH1 TX goes on EP2 OUT
 *  - RX frames arrive as raw 21-byte packets on EP2/EP3 IN
 *  - Both channels must be initialized for stable RX after TX
 *
 * v0.5.2 fixes:
 *  - Track per-channel open state with atomic open_count
 *  - On open: always INIT both channels (required by HW), but only
 *    BAUD+START the channel being opened; skip the other unless already up
 *  - On close: only RESET the channel being closed
 *  - Add TX endpoint debug logging via dynamic debug (use pr_debug)
 *  - This makes can0 and can1 independently controllable without
 *    accidentally activating the physical partner port
 *
 * v0.5.3 fixes:
 *  - Restore required full dual-channel init sequence per PROTOCOL.md:
 *    INIT ch0 → INIT ch1 → BAUD ch0 → START ch0 → BAUD ch1 → START ch1
 *    (firmware stops EP3 RX delivery if ch1 is not fully started)
 *  - Set TX payload byte [2] = channel_index for CH1 frames
 *    (mirrors INIT_CAN format; device may use this for port routing)
 *  - Send CH1 TX on EP1 OUT with channel byte set in payload
 *    (reverse-engineered evidence suggests device uses payload[2], not EP,
 *     to select the physical CAN port for TX)
 *
 * Author: Manuel Rösel
 * manuel.roesel@ros-it.ch
 * Reverse-engineered from USB captures and libcontrolcanfd.a
 * License: GPL v2
 */

#include <linux/module.h>
#include <linux/usb.h>
#include <linux/slab.h>
#include <linux/netdevice.h>
#include <linux/can.h>
#include <linux/can/dev.h>
#include <linux/can/error.h>
#include <linux/can/skb.h>
#include <linux/timekeeping.h>
#include <crypto/skcipher.h>
#include <linux/scatterlist.h>

#define DRIVER_VERSION	"0.5.3"
#define DRIVER_NAME	"zcan_usb"

#define ZCAN_VENDOR_ID		0x04d8
#define ZCAN_PRODUCT_ID		0x0053

/* -------------------------------------------------------------------------
 * Protocol constants
 * -------------------------------------------------------------------------
 * All commands use BEEF/DEAD framing:
 *   BE EF | datalen(2BE) | pkgall(1) | pkgcur(1) | cmd(2BE) |
 *   data[datalen] | check(2) | DE AD
 *
 * Checksum = datalen + 2*cmd + (pkgall<<8) + (pkgcur<<8)
 *           + sum_of_16bit_BE_words(data) + 0xBEAD
 */
#define PKG_HEAD_B0		0xBE
#define PKG_HEAD_B1		0xEF
#define PKG_TAIL_B0		0xDE
#define PKG_TAIL_B1		0xAD
#define PKG_MAGIC		0xBEADu	/* added to checksum */

/* Command codes (big-endian) */
#define CMD_OPEN_DEVICE		0x8001	/* AES handshake */
#define CMD_GET_INFO		0x8005	/* read device info */
#define CMD_INIT_CAN		0x8002	/* initialize CAN channel */
#define CMD_SET_BAUD		0x800b	/* set bitrate / hardware filter */
#define CMD_START_CAN		0x8003	/* start CAN channel */
#define CMD_TRANSMIT		0x8004	/* transmit CAN frame */
#define CMD_RESET_CAN		0x8008	/* reset / stop channel */

/* USB endpoints */
#define EP_CMD_OUT		0x01	/* commands + CH0 TX out */
#define EP_CMD_IN		0x81	/* command responses in */
#define EP_CH1_TX		0x02	/* CH1 TX out */
#define EP_CH0_RX		0x82	/* CH0 RX in (frames received by CH0) */
#define EP_CH1_RX		0x83	/* CH1 RX in (frames received by CH1) */

/* TX frame type markers */
#define TX_TYPE_CAN		0xF1	/* classic CAN frame */
#define TX_TYPE_CANFD		0xF2	/* CAN FD frame */

/* TX frame layout (classic CAN, payload = 26 bytes):
 *  [0]     = 0x55  magic
 *  [1]     = 0xF1  classic CAN
 *  [2..7]  = 0x00  reserved
 *  [8..9]  = CAN ID, big-endian 16-bit
 *  [10]    = DLC
 *  [11..11+dlc-1] = data bytes
 *  [21]    = transmit_type (0x00 = normal with auto-retry)
 */
#define TX_CAN_PAYLOAD_LEN	26
#define TX_CAN_ID_OFFSET	8
#define TX_CAN_DLC_OFFSET	10
#define TX_CAN_DATA_OFFSET	11
#define TX_CAN_TXTYPE_OFFSET	21

/* RX frame layout (raw, 21 bytes, no BEEF wrapper):
 *  [0..1]  = flags / partial timestamp
 *  [2..3]  = CAN_ID << 2, little-endian → CAN_ID = ([3]<<8 | [2]) >> 2
 *  [4..5]  = 0x00 0x00
 *  [6]     = lower nibble = DLC
 *  [7]     = 0xFF (fixed)
 *  [8..8+dlc-1] = CAN data
 *  [9+dlc..20]  = padding
 */
#define RX_FRAME_SIZE		21
#define RX_DLC_OFFSET		6
#define RX_DATA_OFFSET		8

/* Driver limits */
#define ZCAN_MAX_PKG_SIZE	512
#define ZCAN_RX_BUF_SIZE	1024
#define ZCAN_MAX_CHANNELS	2
#define ZCAN_NUM_RX_URBS	2	/* one per RX endpoint */

/* Magic bytes in init/baud commands */
#define MAGIC_INIT		0x55
#define MAGIC_BAUD		0x7E

/* -------------------------------------------------------------------------
 * Data structures
 * -------------------------------------------------------------------------
 */
struct zcan_priv;

struct zcan_channel {
	struct can_priv		 can;		/* must be first for can_priv cast */
	struct zcan_priv	*priv;
	struct net_device	*netdev;
	int			 channel_idx;
};

struct zcan_priv {
	struct usb_device	*udev;
	struct net_device	*netdevs[ZCAN_MAX_CHANNELS];
	int			 num_channels;

	/*
	 * Per-channel open state.
	 * Incremented in open, decremented in stop.
	 * Used to decide whether to start/stop the "other" channel.
	 */
	atomic_t		 ch_open[ZCAN_MAX_CHANNELS];

	/* Persistent RX URBs - one per data endpoint */
	struct urb		*rx_urbs[ZCAN_NUM_RX_URBS];
	u8			*rx_bufs[ZCAN_NUM_RX_URBS];

	/* Per-channel async TX URBs */
	struct urb		*tx_urbs[ZCAN_MAX_CHANNELS];
	u8			*tx_bufs[ZCAN_MAX_CHANNELS];
	atomic_t		 tx_active[ZCAN_MAX_CHANNELS];

	/* Command serialization (probe/open/close use blocking bulk_msg) */
	struct mutex		 cmd_lock;
	u8			 cmd_buf[ZCAN_MAX_PKG_SIZE];
	u8			 resp_buf[ZCAN_MAX_PKG_SIZE];
};

/* -------------------------------------------------------------------------
 * Protocol helpers
 * -------------------------------------------------------------------------
 */

/*
 * Calculate packet checksum.
 * Formula reverse-engineered from gen_package() in gvar.o (libcontrolcanfd.a):
 *   sum = datalen + 2*cmd + (pkgall<<8) + (pkgcur<<8)
 *       + sum_of_16bit_BE_words(data[])
 *       + PKG_MAGIC (0xBEAD)
 */
static u16 zcan_calc_checksum(const u8 *buf)
{
	u16 datalen = ((u16)buf[2] << 8) | buf[3];
	u16 cmd     = ((u16)buf[6] << 8) | buf[7];
	u8  pkgall  = buf[4];
	u8  pkgcur  = buf[5];
	const u8 *data = buf + 8;
	u32 sum;
	int i;

	sum = (u32)datalen + 2u * (u32)cmd
	    + ((u32)pkgall << 8) + ((u32)pkgcur << 8);

	for (i = 0; i + 1 < (int)datalen; i += 2)
		sum += ((u16)data[i] << 8) | data[i + 1];

	sum += PKG_MAGIC;
	return (u16)(sum & 0xFFFF);
}

/*
 * Build a BEEF/DEAD framed command packet into buf.
 * Returns total packet length.
 */
static int zcan_build_pkt(u8 *buf, u16 cmd, const u8 *data, u16 datalen)
{
	u16 check;
	int total;

	buf[0] = PKG_HEAD_B0;
	buf[1] = PKG_HEAD_B1;
	buf[2] = (datalen >> 8) & 0xff;
	buf[3] =  datalen       & 0xff;
	buf[4] = 0x01;  /* pkgall */
	buf[5] = 0x01;  /* pkgcur */
	buf[6] = (cmd >> 8) & 0xff;
	buf[7] =  cmd       & 0xff;

	if (data && datalen)
		memcpy(buf + 8, data, datalen);

	total = 8 + datalen;
	check = zcan_calc_checksum(buf);

	buf[total]     = (check >> 8) & 0xff;
	buf[total + 1] =  check       & 0xff;
	buf[total + 2] = PKG_TAIL_B0;
	buf[total + 3] = PKG_TAIL_B1;

	return total + 4;
}

/*
 * Send a command on EP1 OUT and optionally wait for a BEEF response on EP1 IN.
 *
 * The device continuously sends 2-byte polling packets on EP1 IN (every ~16ms).
 * These must be discarded when waiting for a real command response, which is
 * identified by starting with 0xBE 0xEF.
 *
 * Pass resp=NULL to skip reading the response (for commands after netdev_open
 * where EP1 IN is no longer exclusively used for command responses).
 */
static int zcan_cmd(struct zcan_priv *priv, u16 cmd,
		    const u8 *data, u16 datalen,
		    u8 *resp, int *resp_len)
{
	int pkt_len, actual, ret, i;

	pkt_len = zcan_build_pkt(priv->cmd_buf, cmd, data, datalen);

	ret = usb_bulk_msg(priv->udev,
			   usb_sndbulkpipe(priv->udev, EP_CMD_OUT),
			   priv->cmd_buf, pkt_len, &actual, 3000);
	if (ret) {
		dev_err(&priv->udev->dev, "cmd 0x%04x send failed: %d\n",
			cmd, ret);
		return ret;
	}

	if (!resp)
		return 0;

	/* Discard 2-byte polling packets, wait for real BEEF response */
	for (i = 0; i < 20; i++) {
		ret = usb_bulk_msg(priv->udev,
				   usb_rcvbulkpipe(priv->udev, EP_CMD_IN),
				   priv->resp_buf, ZCAN_MAX_PKG_SIZE,
				   &actual, 3000);
		if (ret) {
			dev_err(&priv->udev->dev,
				"cmd 0x%04x recv failed: %d\n", cmd, ret);
			return ret;
		}
		if (actual >= 2 &&
		    priv->resp_buf[0] == PKG_HEAD_B0 &&
		    priv->resp_buf[1] == PKG_HEAD_B1)
			break;
	}

	if (resp_len)
		*resp_len = actual;
	if (actual > 0)
		memcpy(resp, priv->resp_buf, actual);

	return 0;
}

/* -------------------------------------------------------------------------
 * AES-128 ECB (used for device authentication)
 * -------------------------------------------------------------------------
 */

/*
 * Encrypt 16 bytes with AES-128 ECB using the kernel crypto API.
 */
static int zcan_aes128_ecb_encrypt(const u8 key[16], const u8 *in, u8 *out)
{
	struct crypto_skcipher *tfm;
	struct skcipher_request *req;
	struct scatterlist sg_in, sg_out;
	u8 *ibuf, *obuf;
	int ret;

	tfm = crypto_alloc_skcipher("ecb(aes)", 0, 0);
	if (IS_ERR(tfm))
		return PTR_ERR(tfm);

	ret = crypto_skcipher_setkey(tfm, key, 16);
	if (ret)
		goto free_tfm;

	req = skcipher_request_alloc(tfm, GFP_KERNEL);
	if (!req) { ret = -ENOMEM; goto free_tfm; }

	ibuf = kmemdup(in, 16, GFP_KERNEL);
	obuf = kmalloc(16, GFP_KERNEL);
	if (!ibuf || !obuf) { ret = -ENOMEM; goto free_bufs; }

	sg_init_one(&sg_in,  ibuf, 16);
	sg_init_one(&sg_out, obuf, 16);
	skcipher_request_set_crypt(req, &sg_in, &sg_out, 16, NULL);

	ret = crypto_skcipher_encrypt(req);
	if (!ret)
		memcpy(out, obuf, 16);

free_bufs:
	kfree(ibuf);
	kfree(obuf);
	skcipher_request_free(req);
free_tfm:
	crypto_free_skcipher(tfm);
	return ret;
}

/* -------------------------------------------------------------------------
 * Device initialization sequence
 * -------------------------------------------------------------------------
 */

/*
 * CMD_OPEN_DEVICE (0x8001) - AES-128 challenge-response handshake.
 *
 * Protocol (reverse-engineered from libcontrolcanfd.a):
 *  1. Build 32-byte challenge with fixed magic bytes and timestamp
 *  2. Send challenge plaintext to device
 *  3. Device responds with AES_128_ECB(key, challenge[0:16])
 *                                    + AES_128_ECB(key, challenge[16:32])
 *  4. Driver verifies response matches expected encryption
 *
 * AES-128 key extracted from controlcanfd.o via GDB on statically linked binary.
 * Key: 61 62 0B 1A 65 74 63 70 3B 40 75 00 38 22 71 65
 *
 * Challenge format:
 *  [0..1]  = 0xDE 0xFF  (magic)
 *  [2..5]  = 0x00 (MUST be zero - device rejects non-zero)
 *  [6..7]  = 0x43 0x01  (fixed)
 *  [8..11] = 0xF2 0x89 0x82 0xEE  (fixed)
 *  [12..15]= timestamp (32-bit)
 *  [16..21]= 0xD0 0x1F 0x07 0x11 0x5F 0x68  (fixed)
 *  [22..25]= random value
 *  [26..31]= 0xC2 0x3E 0xC8 0x26 0x52 0x36  (fixed)
 */
static int zcan_open_device(struct zcan_priv *priv)
{
	static const u8 aes_key[16] = {
		0x61, 0x62, 0x0b, 0x1a, 0x65, 0x74, 0x63, 0x70,
		0x3b, 0x40, 0x75, 0x00, 0x38, 0x22, 0x71, 0x65
	};
	u8 challenge[32] = {};
	u8 expected[32]  = {};
	u8 resp[ZCAN_MAX_PKG_SIZE];
	int resp_len = 0;
	struct timespec64 ts;
	u32 rand_val;
	int ret;

	ktime_get_real_ts64(&ts);
	rand_val = (u32)(ts.tv_nsec / 1000);

	/* Fixed magic */
	challenge[0]  = 0xde;  challenge[1]  = 0xff;
	/* [2..5] must be zero */
	challenge[6]  = 0x43;  challenge[7]  = 0x01;
	challenge[8]  = 0xf2;  challenge[9]  = 0x89;
	challenge[10] = 0x82;  challenge[11] = 0xee;
	/* Timestamp */
	challenge[12] = (rand_val >> 24) & 0xff;
	challenge[13] = (rand_val >> 16) & 0xff;
	challenge[14] = (rand_val >>  8) & 0xff;
	challenge[15] =  rand_val        & 0xff;
	/* Fixed */
	challenge[16] = 0xd0;  challenge[17] = 0x1f;
	challenge[18] = 0x07;  challenge[19] = 0x11;
	challenge[20] = 0x5f;  challenge[21] = 0x68;
	/* Random */
	rand_val = (u32)(ts.tv_sec ^ ts.tv_nsec);
	challenge[22] = (rand_val >> 24) & 0xff;
	challenge[23] = (rand_val >> 16) & 0xff;
	challenge[24] = (rand_val >>  8) & 0xff;
	challenge[25] =  rand_val        & 0xff;
	/* Fixed tail */
	challenge[26] = 0xc2;  challenge[27] = 0x3e;
	challenge[28] = 0xc8;  challenge[29] = 0x26;
	challenge[30] = 0x52;  challenge[31] = 0x36;

	/* Pre-compute expected response */
	ret  = zcan_aes128_ecb_encrypt(aes_key, challenge,      expected);
	ret |= zcan_aes128_ecb_encrypt(aes_key, challenge + 16, expected + 16);
	if (ret)
		dev_warn(&priv->udev->dev, "AES setup failed: %d\n", ret);

	ret = zcan_cmd(priv, CMD_OPEN_DEVICE, challenge, sizeof(challenge),
		       resp, &resp_len);
	if (ret)
		return 0; /* non-fatal, continue */

	if (resp_len >= 8 + 32 &&
	    resp[0] == PKG_HEAD_B0 && resp[1] == PKG_HEAD_B1 &&
	    memcmp(resp + 8, expected, 32) == 0)
		dev_info(&priv->udev->dev, "device handshake OK\n");
	else
		dev_warn(&priv->udev->dev,
			 "handshake: unexpected response len=%d\n", resp_len);

	return 0;
}

/*
 * CMD_GET_INFO (0x8005) - Read device firmware/hardware version and serial.
 */
static int zcan_get_info(struct zcan_priv *priv)
{
	u8 resp[ZCAN_MAX_PKG_SIZE];
	int resp_len = 0, ret;

	ret = zcan_cmd(priv, CMD_GET_INFO, NULL, 0, resp, &resp_len);
	if (ret)
		return ret;

	if (resp_len >= 14)
		dev_info(&priv->udev->dev,
			 "fw=0x%02x%02x hw=0x%02x%02x serial=%.20s\n",
			 resp[8], resp[9], resp[10], resp[11],
			 resp_len > 22 ? (char *)&resp[22] : "?");
	return 0;
}

/* -------------------------------------------------------------------------
 * Baud rate table
 * -------------------------------------------------------------------------
 * Values reverse-engineered from USB captures of ZCAN_SetAbitBaud + InitCAN.
 * dbit_timing: used in INIT_CAN payload bytes [18..21]
 * baud_code:   used in SET_BAUD payload bytes [2..3]
 */
static const struct {
	u32 bitrate;
	u32 dbit_timing;
	u16 baud_code;
} zcan_baud_table[] = {
	{ 1000000, 0x00022e0b, 0x007f },
	{  500000, 0x00025e17, 0x0fff },
	{  250000, 0x0014be2f, 0x1fff },
	{  125000, 0x0114be2f, 0x3fff },
};

static u32 zcan_dbit_timing(u32 bitrate)
{
	int i;
	for (i = 0; i < ARRAY_SIZE(zcan_baud_table); i++)
		if (zcan_baud_table[i].bitrate == bitrate)
			return zcan_baud_table[i].dbit_timing;
	return 0x00025e17; /* default 500k */
}

static u16 zcan_baud_code(u32 bitrate)
{
	int i;
	for (i = 0; i < ARRAY_SIZE(zcan_baud_table); i++)
		if (zcan_baud_table[i].bitrate == bitrate)
			return zcan_baud_table[i].baud_code;
	return 0x0fff; /* default 500k */
}

/*
 * CMD_INIT_CAN (0x8002) - Configure CAN channel timing and mode.
 *
 * Payload format (32 bytes), verified from USB captures:
 *  [0]     = 0x55  magic
 *  [1]     = 0x02  fixed
 *  [2]     = channel index (0 or 1)
 *  [3]     = 0x01  type = CANFD mode (required even for classic CAN)
 *  [4..5]  = 0x00 0x00
 *  [6..9]  = acc_code = 0x00000001
 *  [10..13]= acc_mask = 0xFFFFFFFF
 *  [14..17]= abit_timing = 0x00000000
 *  [18..21]= dbit_timing (bitrate-dependent, see zcan_baud_table)
 *  [22..25]= 0x03 0x01 0x0A 0x02  (fixed, from capture)
 *  [26..29]= 0x00000000
 *  [30]    = 0x00  (termination disabled - hardware always has 120Ω)
 *  [31]    = 0x00
 */
static int zcan_init_channel(struct zcan_priv *priv, int ch)
{
	struct zcan_channel *zch = netdev_priv(priv->netdevs[ch]);
	u32 bitrate = zch->can.bittiming.bitrate ?: 500000;
	u32 dbit    = zcan_dbit_timing(bitrate);
	u8 data[32] = {
		0x55, 0x02,
		(u8)ch, 0x01,		/* channel, type (must be 0x01) */
		0x00, 0x00,
		0x00, 0x00, 0x00, 0x01,	/* acc_code */
		0xff, 0xff, 0xff, 0xff,	/* acc_mask */
		0x00, 0x00, 0x00, 0x00,	/* abit_timing */
		0x00, 0x00, 0x00, 0x00,	/* dbit_timing (filled below) */
		0x03, 0x01, 0x0a, 0x02,	/* fixed (from capture) */
		0x00, 0x00, 0x00, 0x00,
		0x00, 0x00		/* termination off */
	};
	u8 resp[ZCAN_MAX_PKG_SIZE];
	int resp_len = 0;

	data[18] = (dbit >> 24) & 0xff;
	data[19] = (dbit >> 16) & 0xff;
	data[20] = (dbit >>  8) & 0xff;
	data[21] =  dbit        & 0xff;

	return zcan_cmd(priv, CMD_INIT_CAN, data, sizeof(data),
			resp, &resp_len);
}

/*
 * CMD_SET_BAUD (0x800B, 4-byte variant) - Set channel bitrate.
 */
static int zcan_set_baud(struct zcan_priv *priv, int ch, u32 bitrate)
{
	u16 code = zcan_baud_code(bitrate);
	u8 data[4] = {
		MAGIC_BAUD, (u8)ch,
		(code >> 8) & 0xff, code & 0xff
	};
	u8 resp[ZCAN_MAX_PKG_SIZE];
	int resp_len = 0;

	return zcan_cmd(priv, CMD_SET_BAUD, data, sizeof(data),
			resp, &resp_len);
}

/*
 * CMD_START_CAN (0x8003) - Start CAN channel (enable bus).
 */
static int zcan_start_channel(struct zcan_priv *priv, int ch)
{
	u8 data[4] = { 0x55, 0x80, 0x03, (u8)ch };
	u8 resp[ZCAN_MAX_PKG_SIZE];
	int resp_len = 0;

	return zcan_cmd(priv, CMD_START_CAN, data, sizeof(data),
			resp, &resp_len);
}

/* -------------------------------------------------------------------------
 * RX path
 * -------------------------------------------------------------------------
 */

/*
 * Parse a received CAN frame from a 21-byte raw RX packet.
 *
 * RX frame format (verified from live USB captures, fw=0x0200 hw=0x0212):
 *  [0..1]  = flags / partial timestamp
 *  [2..3]  = CAN_ID << 2, little-endian
 *  [4..5]  = 0x00 0x00
 *  [6]     = lower nibble = DLC  (NOT upper nibble)
 *  [7]     = 0xFF (fixed marker)
 *  [8..8+dlc-1] = CAN data bytes
 *
 * Endpoint routing:
 *  EP2 IN (0x82) → frames received by CH0
 *  EP3 IN (0x83) → frames received by CH1
 */
static void zcan_rx_frame(struct zcan_channel *ch, const u8 *buf, int len)
{
	struct net_device *netdev = ch->netdev;
	struct can_frame *cf;
	struct sk_buff *skb;
	u32 can_id;
	u8 dlc;

	if (len < 9)
		return;

	can_id = (((u32)buf[3] << 8) | buf[2]) >> 2;
	dlc    = buf[RX_DLC_OFFSET] & 0x0f;

	if (dlc > 8)
		dlc = 8;

	skb = alloc_can_skb(netdev, &cf);
	if (!skb)
		return;

	cf->can_id  = can_id & CAN_SFF_MASK;
	cf->can_dlc = dlc;
	if (len >= RX_DATA_OFFSET + (int)dlc)
		memcpy(cf->data, buf + RX_DATA_OFFSET, dlc);

	netdev->stats.rx_packets++;
	netdev->stats.rx_bytes += dlc;
	netif_rx(skb);
}

static void zcan_rx_complete(struct urb *urb)
{
	struct zcan_priv *priv = urb->context;
	u8 *buf = urb->transfer_buffer;
	int len = urb->actual_length;
	int ret, ch_idx, offset;

	switch (urb->status) {
	case 0:
		break;
	case -ENOENT:
	case -EPIPE:
	case -EPROTO:
	case -ESHUTDOWN:
		return;
	default:
		goto resubmit;
	}

	/* Determine channel from endpoint:
	 * EP2 IN (0x82, pipe endpoint 2) → CH0, EP3 IN (0x83) → CH1 */
	ch_idx = (usb_pipeendpoint(urb->pipe) == 2) ? 0 : 1;

	/* Parse RX frames (21 bytes each, may have multiple per URB) */
	offset = 0;
	while (offset + 9 <= len) {
		u8 *frame = buf + offset;

		/* Skip empty/null frames */
		if (frame[2] == 0 && frame[3] == 0 && frame[6] == 0)
			break;

		if (ch_idx < priv->num_channels && priv->netdevs[ch_idx]) {
			struct zcan_channel *rch =
				netdev_priv(priv->netdevs[ch_idx]);
			zcan_rx_frame(rch, frame,
				      min(len - offset, RX_FRAME_SIZE));
		}
		offset += RX_FRAME_SIZE;
	}

resubmit:
	ret = usb_submit_urb(urb, GFP_ATOMIC);
	if (ret)
		dev_err(&priv->udev->dev,
			"rx urb resubmit ep%d failed: %d\n",
			usb_pipeendpoint(urb->pipe), ret);
}

/* -------------------------------------------------------------------------
 * TX path
 * -------------------------------------------------------------------------
 */

static void zcan_tx_complete(struct urb *urb)
{
	struct net_device *netdev = urb->context;
	struct zcan_channel *ch = netdev_priv(netdev);
	struct zcan_priv *priv = ch->priv;

	if (urb->status) {
		dev_err(&priv->udev->dev,
			"TX completion error ch=%d urb_status=%d\n",
			ch->channel_idx, urb->status);
		netdev->stats.tx_errors++;
	}

	atomic_set(&priv->tx_active[ch->channel_idx], 0);
	netif_wake_queue(netdev);
}

/*
 * Build and transmit a classic CAN frame.
 *
 * TX frame format (classic CAN, payload = 26 bytes, BEEF-wrapped):
 *  [0]     = 0x55  magic
 *  [1]     = 0xF1  classic CAN marker (0xF2 = CAN FD)
 *  [2]     = channel index (0 for CH0, 1 for CH1)
 *             This mirrors the INIT_CAN payload format where [2]=channel.
 *             The device firmware uses this byte to select the physical port.
 *  [3..7]  = 0x00  reserved
 *  [8..9]  = CAN ID, big-endian 16-bit (11-bit SFF)
 *  [10]    = DLC (0..8)
 *  [11..11+dlc-1] = data
 *  [21]    = transmit_type = 0x00 (normal, auto-retry on error)
 *
 * Channel routing (v0.5.3):
 *  Both CH0 and CH1 TX are sent on EP1 OUT (0x01).
 *  The device uses payload byte [2] (channel index) to select the
 *  physical CAN port, not the USB endpoint.
 *  EP2 OUT (0x02) may also work for CH1, but payload[2] is the
 *  definitive channel selector based on reverse-engineering evidence.
 */
static netdev_tx_t zcan_netdev_xmit(struct sk_buff *skb,
				    struct net_device *netdev)
{
	struct zcan_channel *ch = netdev_priv(netdev);
	struct zcan_priv *priv = ch->priv;
	int ch_idx = ch->channel_idx;
	u8 tx_data[TX_CAN_PAYLOAD_LEN];
	int dlc, pkt_len;

	if (can_dropped_invalid_skb(netdev, skb))
		return NETDEV_TX_OK;

	if (atomic_cmpxchg(&priv->tx_active[ch_idx], 0, 1) != 0) {
		netif_stop_queue(netdev);
		return NETDEV_TX_BUSY;
	}

	memset(tx_data, 0, sizeof(tx_data));
	tx_data[0] = MAGIC_INIT;		/* 0x55 */
	tx_data[1] = TX_TYPE_CAN;		/* 0xF1 */

	if (can_is_canfd_skb(skb)) {
		struct canfd_frame *cf = (struct canfd_frame *)skb->data;
		u32 can_id = cf->can_id & CAN_EFF_MASK;

		tx_data[1] = TX_TYPE_CANFD;
		tx_data[TX_CAN_ID_OFFSET]     = (can_id >> 8) & 0xff;
		tx_data[TX_CAN_ID_OFFSET + 1] =  can_id       & 0xff;
		tx_data[TX_CAN_DLC_OFFSET]    = cf->len;
		dlc = cf->len;
		memcpy(tx_data + TX_CAN_DATA_OFFSET, cf->data, dlc);
	} else {
		struct can_frame *cf = (struct can_frame *)skb->data;
		u32 can_id = cf->can_id & CAN_SFF_MASK;

		tx_data[TX_CAN_ID_OFFSET]     = (can_id >> 8) & 0xff;
		tx_data[TX_CAN_ID_OFFSET + 1] =  can_id       & 0xff;
		tx_data[TX_CAN_DLC_OFFSET]    = cf->can_dlc;
		dlc = cf->can_dlc;
		memcpy(tx_data + TX_CAN_DATA_OFFSET, cf->data, dlc);
	}
	/* transmit_type = 0x00: normal send with auto-retry */
	tx_data[TX_CAN_TXTYPE_OFFSET] = 0x00;

	/*
	 * Hardware Routing Logic discovered via USB sniffing the official SDK:
	 * Byte 20 is a port bitmask or routing index.
	 * 0x00 on EP1 OUT -> routes to CAN1
	 * 0x02 on EP2 OUT -> routes to CAN2
	 */
	if (ch_idx == 1) {
		tx_data[20] = 0x02;
	} else {
		tx_data[20] = 0x00;
	}

	unsigned int ep_out = (ch_idx == 0) ? EP_CMD_OUT : EP_CH1_TX;

	pkt_len = zcan_build_pkt(priv->tx_bufs[ch_idx], CMD_TRANSMIT,
				 tx_data, TX_CAN_PAYLOAD_LEN);

	dev_dbg(&priv->udev->dev,
		"TX ch=%d ep=0x%02x payload[2]=%d can_id=0x%03x dlc=%d\n",
		ch_idx, ep_out, tx_data[2],
		(tx_data[TX_CAN_ID_OFFSET] << 8) | tx_data[TX_CAN_ID_OFFSET+1],
		dlc);

	usb_fill_bulk_urb(priv->tx_urbs[ch_idx], priv->udev,
			  usb_sndbulkpipe(priv->udev, ep_out),
			  priv->tx_bufs[ch_idx], pkt_len,
			  zcan_tx_complete, netdev);

	{
		int submit_ret = usb_submit_urb(priv->tx_urbs[ch_idx], GFP_ATOMIC);
		if (submit_ret) {
			u32 debug_can_id;
			if (can_is_canfd_skb(skb)) {
				struct canfd_frame *cf = (struct canfd_frame *)skb->data;
				debug_can_id = cf->can_id & CAN_SFF_MASK;
			} else {
				struct can_frame *cf = (struct can_frame *)skb->data;
				debug_can_id = cf->can_id & CAN_SFF_MASK;
			}
			dev_err(&priv->udev->dev,
				"TX submit failed ch=%d ep=0x%02x can_id=0x%03x dlc=%d ret=%d\n",
				ch_idx, ep_out, debug_can_id, dlc, submit_ret);
			netdev->stats.tx_errors++;
			atomic_set(&priv->tx_active[ch_idx], 0);
			dev_kfree_skb(skb);
			return NETDEV_TX_OK;
		}
	}

	netdev->stats.tx_packets++;
	netdev->stats.tx_bytes += dlc;
	dev_kfree_skb(skb);
	return NETDEV_TX_OK;
}

/* -------------------------------------------------------------------------
 * netdev callbacks
 * -------------------------------------------------------------------------
 */

/*
 * Open a CAN channel.
 *
 * The device requires INIT on BOTH channels before it will deliver
 * stable RX frames (firmware constraint, verified from USB captures).
 *
 * v0.5.3 behaviour:
 *   Per PROTOCOL.md, the required init sequence is:
 *     INIT ch0 → INIT ch1 → BAUD ch0 → START ch0 → BAUD ch1 → START ch1
 *   Both channels MUST be fully BAUD+STARTed or EP3 stops delivering RX.
 *   We track which channels are open so we can re-apply the correct
 *   bitrate for each channel when either is opened.
 */
static int zcan_netdev_open(struct net_device *netdev)
{
	struct zcan_channel *ch = netdev_priv(netdev);
	struct zcan_priv *priv = ch->priv;
	int my_ch  = ch->channel_idx;
	int other  = 1 - my_ch;
	u32 baud   = ch->can.bittiming.bitrate ?: 500000;
	int ret;
	/* Determine bitrate for the other channel */
	struct zcan_channel *other_ch = netdev_priv(priv->netdevs[other]);
	u32 other_baud = (other_ch->can.bittiming.bitrate) ?
			 other_ch->can.bittiming.bitrate : baud;

	ret = open_candev(netdev);
	if (ret)
		return ret;

	mutex_lock(&priv->cmd_lock);

	/*
	 * Step 1: INIT both channels (firmware requirement — even for single
	 * channel use, both must be initialized or RX becomes unreliable).
	 */
	ret = zcan_init_channel(priv, 0);
	if (ret) {
		dev_err(&priv->udev->dev, "INIT_CAN ch0 failed: %d\n", ret);
		goto out;
	}
	ret = zcan_init_channel(priv, 1);
	if (ret) {
		dev_err(&priv->udev->dev, "INIT_CAN ch1 failed: %d\n", ret);
		goto out;
	}

	/*
	 * Step 2: BAUD + START ch0 always (firmware requires the sequence
	 * ch0 first, then ch1 — matches vendor library USB capture order).
	 */
	ret = zcan_set_baud(priv, 0, (my_ch == 0) ? baud : other_baud);
	if (ret) {
		dev_err(&priv->udev->dev, "SET_BAUD ch0 failed: %d\n", ret);
		goto out;
	}
	ret = zcan_start_channel(priv, 0);
	if (ret) {
		dev_err(&priv->udev->dev, "START_CAN ch0 failed: %d\n", ret);
		goto out;
	}

	/*
	 * Step 3: BAUD + START ch1.
	 */
	ret = zcan_set_baud(priv, 1, (my_ch == 1) ? baud : other_baud);
	if (ret) {
		dev_err(&priv->udev->dev, "SET_BAUD ch1 failed: %d\n", ret);
		goto out;
	}
	ret = zcan_start_channel(priv, 1);
	if (ret) {
		dev_err(&priv->udev->dev, "START_CAN ch1 failed: %d\n", ret);
		goto out;
	}

	atomic_inc(&priv->ch_open[my_ch]);

	ch->can.state = CAN_STATE_ERROR_ACTIVE;
	netif_start_queue(netdev);
	dev_info(&priv->udev->dev,
		 "channel %d opened at %u bps (TX via EP1, payload[2]=%d)\n",
		 my_ch, baud, my_ch);

out:
	mutex_unlock(&priv->cmd_lock);
	if (ret)
		close_candev(netdev);
	return ret;
}

static int zcan_netdev_stop(struct net_device *netdev)
{
	struct zcan_channel *ch = netdev_priv(netdev);
	struct zcan_priv *priv = ch->priv;
	int my_ch = ch->channel_idx;
	u8 data[4] = { 0x55, 0x80, 0x03, (u8)my_ch };
	u8 resp[ZCAN_MAX_PKG_SIZE];
	int resp_len;

	netif_stop_queue(netdev);
	ch->can.state = CAN_STATE_STOPPED;

	mutex_lock(&priv->cmd_lock);
	zcan_cmd(priv, CMD_RESET_CAN, data, sizeof(data), resp, &resp_len);
	mutex_unlock(&priv->cmd_lock);

	atomic_dec(&priv->ch_open[my_ch]);

	close_candev(netdev);
	dev_info(&priv->udev->dev, "channel %d closed\n", my_ch);
	return 0;
}

/*
 * do_set_bittiming callback.
 * Skip if the channel is not yet open (called by 'ip link set bitrate'
 * before 'ip link set up'). The correct baud rate is applied during open.
 */
static int zcan_set_bittiming(struct net_device *netdev)
{
	struct zcan_channel *ch = netdev_priv(netdev);
	struct zcan_priv *priv = ch->priv;
	int ret = 0;

	if (ch->can.state == CAN_STATE_STOPPED)
		return 0;

	mutex_lock(&priv->cmd_lock);
	ret = zcan_set_baud(priv, ch->channel_idx,
			    ch->can.bittiming.bitrate);
	mutex_unlock(&priv->cmd_lock);
	return ret;
}

static void zcan_set_rx_mode(struct net_device *netdev)
{
	/* Hardware filtering not needed; accept all in default config */
}

static const struct net_device_ops zcan_netdev_ops = {
	.ndo_open	 = zcan_netdev_open,
	.ndo_stop	 = zcan_netdev_stop,
	.ndo_start_xmit	 = zcan_netdev_xmit,
	.ndo_set_rx_mode = zcan_set_rx_mode,
};

/* -------------------------------------------------------------------------
 * Bittiming constants
 * -------------------------------------------------------------------------
 * Used by the SocketCAN framework to validate user-supplied bitrates.
 */
static const struct can_bittiming_const zcan_bittiming_const = {
	.name      = DRIVER_NAME,
	.tseg1_min = 1,
	.tseg1_max = 16,
	.tseg2_min = 1,
	.tseg2_max = 8,
	.sjw_max   = 4,
	.brp_min   = 1,
	.brp_max   = 64,
	.brp_inc   = 1,
};

/* -------------------------------------------------------------------------
 * USB probe / disconnect
 * -------------------------------------------------------------------------
 */

static int zcan_probe(struct usb_interface *intf,
		      const struct usb_device_id *id)
{
	struct usb_device *udev = interface_to_usbdev(intf);
	struct zcan_priv *priv;
	int i, ret;

	priv = kzalloc(sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;

	priv->udev = udev;
	priv->num_channels = ZCAN_MAX_CHANNELS;
	mutex_init(&priv->cmd_lock);
	usb_set_intfdata(intf, priv);

	/* Initialize per-channel open counters to zero */
	for (i = 0; i < ZCAN_MAX_CHANNELS; i++)
		atomic_set(&priv->ch_open[i], 0);

	/*
	 * Device needs ~1.5s after USB enumeration before it accepts
	 * the AES handshake. Without this delay CMD_OPEN_DEVICE times out.
	 */
	msleep(1500);

	ret = zcan_open_device(priv);
	if (ret)
		dev_warn(&udev->dev, "handshake failed: %d\n", ret);

	ret = zcan_get_info(priv);
	if (ret)
		dev_warn(&udev->dev, "get_info failed: %d\n", ret);

	/* Register a netdev (canX) for each channel */
	for (i = 0; i < priv->num_channels; i++) {
		struct net_device *netdev;
		struct zcan_channel *ch;

		netdev = alloc_candev(sizeof(*ch), 1);
		if (!netdev) {
			ret = -ENOMEM;
			goto err_free;
		}

		ch = netdev_priv(netdev);
		ch->priv        = priv;
		ch->netdev      = netdev;
		ch->channel_idx = i;

		netdev->netdev_ops = &zcan_netdev_ops;
		netdev->flags     |= IFF_ECHO;

		ch->can.clock.freq         = 80000000;
		ch->can.bittiming_const    = &zcan_bittiming_const;
		ch->can.do_set_bittiming   = zcan_set_bittiming;
		ch->can.ctrlmode_supported = CAN_CTRLMODE_LOOPBACK;

		SET_NETDEV_DEV(netdev, &intf->dev);

		ret = register_candev(netdev);
		if (ret) {
			free_candev(netdev);
			goto err_free;
		}

		priv->netdevs[i] = netdev;
		dev_info(&udev->dev, "registered %s (channel %d, tx_ep=0x%02x)\n",
			 netdev->name, i,
			 (i == 0) ? EP_CMD_OUT : EP_CH1_TX);
	}

	/* Setup RX URBs - one per data endpoint */
	{
		unsigned int rx_eps[ZCAN_NUM_RX_URBS] = {
			EP_CH0_RX,	/* EP2 IN (0x82): received by CH0 */
			EP_CH1_RX,	/* EP3 IN (0x83): received by CH1 */
		};

		for (i = 0; i < ZCAN_NUM_RX_URBS; i++) {
			struct urb *urb;
			u8 *buf;

			urb = usb_alloc_urb(0, GFP_KERNEL);
			if (!urb)
				break;

			buf = kmalloc(ZCAN_RX_BUF_SIZE, GFP_KERNEL);
			if (!buf) {
				usb_free_urb(urb);
				break;
			}

			usb_fill_bulk_urb(urb, udev,
					  usb_rcvbulkpipe(udev, rx_eps[i]),
					  buf, ZCAN_RX_BUF_SIZE,
					  zcan_rx_complete, priv);
			priv->rx_urbs[i] = urb;
			priv->rx_bufs[i] = buf;
			usb_submit_urb(urb, GFP_KERNEL);
		}
	}

	/* Setup TX URBs - one per channel */
	for (i = 0; i < ZCAN_MAX_CHANNELS; i++) {
		struct urb *urb;
		u8 *buf;

		urb = usb_alloc_urb(0, GFP_KERNEL);
		if (!urb)
			break;

		buf = kmalloc(ZCAN_MAX_PKG_SIZE, GFP_KERNEL);
		if (!buf) {
			usb_free_urb(urb);
			break;
		}

		atomic_set(&priv->tx_active[i], 0);
		priv->tx_urbs[i] = urb;
		priv->tx_bufs[i] = buf;
	}

	dev_info(&udev->dev,
		 "ZCAN USB CANFD connected (%d channels) [v%s]\n"
		 "  CH0 (can0): TX→EP1(0x01) RX←EP2(0x82)\n"
		 "  CH1 (can1): TX→EP2(0x02) RX←EP3(0x83)\n",
		 priv->num_channels, DRIVER_VERSION);
	return 0;

err_free:
	for (i = 0; i < ZCAN_MAX_CHANNELS; i++) {
		if (priv->netdevs[i]) {
			unregister_candev(priv->netdevs[i]);
			free_candev(priv->netdevs[i]);
		}
	}
	kfree(priv);
	return ret;
}

static void zcan_disconnect(struct usb_interface *intf)
{
	struct zcan_priv *priv = usb_get_intfdata(intf);
	int i;

	if (!priv)
		return;

	for (i = 0; i < ZCAN_NUM_RX_URBS; i++) {
		if (priv->rx_urbs[i]) {
			usb_kill_urb(priv->rx_urbs[i]);
			usb_free_urb(priv->rx_urbs[i]);
			kfree(priv->rx_bufs[i]);
		}
	}

	for (i = 0; i < ZCAN_MAX_CHANNELS; i++) {
		if (priv->tx_urbs[i]) {
			usb_kill_urb(priv->tx_urbs[i]);
			usb_free_urb(priv->tx_urbs[i]);
			kfree(priv->tx_bufs[i]);
		}
	}

	for (i = 0; i < ZCAN_MAX_CHANNELS; i++) {
		if (priv->netdevs[i]) {
			unregister_candev(priv->netdevs[i]);
			free_candev(priv->netdevs[i]);
		}
	}

	kfree(priv);
	dev_info(&intf->dev, "ZCAN USB CANFD disconnected\n");
}

static const struct usb_device_id zcan_id_table[] = {
	{ USB_DEVICE(ZCAN_VENDOR_ID, ZCAN_PRODUCT_ID) },
	{ }
};
MODULE_DEVICE_TABLE(usb, zcan_id_table);

static struct usb_driver zcan_driver = {
	.name       = DRIVER_NAME,
	.probe      = zcan_probe,
	.disconnect = zcan_disconnect,
	.id_table   = zcan_id_table,
};

module_usb_driver(zcan_driver);

MODULE_AUTHOR("Manuel Rösel");
MODULE_DESCRIPTION("Linux SocketCAN driver for NXP USB CANFD DEBUG (04d8:0053)");
MODULE_LICENSE("GPL v2");
MODULE_VERSION(DRIVER_VERSION);
