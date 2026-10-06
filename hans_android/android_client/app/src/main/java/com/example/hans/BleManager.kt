package com.example.hans

import android.annotation.SuppressLint
import android.bluetooth.*
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import java.util.LinkedList
import java.util.Queue
import java.util.UUID
import kotlin.math.roundToInt

@SuppressLint("MissingPermission")
enum class DeviceType {
    BRACELET,
    BELT
}

class BleManager(
    private val context: Context,
    private val deviceType: DeviceType
) {

    private var bluetoothGatt: BluetoothGatt? = null
    private val adapter: BluetoothAdapter? = (context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager).adapter
    private val handler = Handler(Looper.getMainLooper())
    private var connectionState = BluetoothProfile.STATE_DISCONNECTED

    // =================================================================
    // UUIDs
    // =================================================================
    private val SERVICE_UUID: UUID
        get() = when (deviceType) {
            DeviceType.BELT ->
                UUID.fromString("0000fe51-0000-1000-8000-00805f9b34fb")

            DeviceType.BRACELET ->
                UUID.fromString("0000fe55-0000-1000-8000-00805f9b34fb")
        }

    private val KEEP_ALIVE_UUID: UUID
        get() = when (deviceType) {
            DeviceType.BELT ->
                UUID.fromString("0000fe02-0000-1000-8000-00805f9b34fb")

            DeviceType.BRACELET ->
                UUID.fromString("0000fe02-0000-1000-8000-00805f9b34fb")
        }

    private val WRITE_UUID: UUID
        get() = when (deviceType) {
            DeviceType.BELT ->
                UUID.fromString("0000fe03-0000-1000-8000-00805f9b34fb")

            DeviceType.BRACELET ->
                UUID.fromString("0000fe15-0000-1000-8000-00805f9b34fb")
        }

    private val PARAM_UUID: UUID
        get() = when (deviceType) {
            DeviceType.BELT ->
                UUID.fromString("0000fe05-0000-1000-8000-00805f9b34fb")

            DeviceType.BRACELET ->
                UUID.fromString("0000fe05-0000-1000-8000-00805f9b34fb")
        }

    private val NOTIFY_UUID: UUID
        get() = when (deviceType) {
            DeviceType.BELT ->
                UUID.fromString("0000fe09-0000-1000-8000-00805f9b34fb")

            DeviceType.BRACELET ->
                UUID.fromString("0000fe16-0000-1000-8000-00805f9b34fb")
        }

    private val BELT_FIRMWARE_UUID = UUID.fromString("0000fe01-0000-1000-8000-00805f9b34fb")

    private val CCCD_UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

    // Robust Command Queue
    private val commandQueue: Queue<Runnable> = LinkedList()
    @Volatile private var isExecuting = false
    private var deviceName = "Unknown"
    private var currentTimeout: Runnable? = null

    interface InformationListener {
        fun onBatteryUpdate(chargeState: Int, batteryLevel: Float)
        fun onFirmwareUpdate(version:String)
    }
    private var informationListener: InformationListener? = null

    fun setInformationListener(listener: InformationListener?) {
        informationListener = listener
    }

    fun connect(deviceAddress: String) {
        val device = adapter?.getRemoteDevice(deviceAddress)
        if (device == null) {
            Log.e("BLE", "Device not found: $deviceAddress")
            return
        }

        deviceName = device.name ?: deviceAddress
        Log.i("BLE", "Connecting to $deviceName ($deviceAddress)...")

        synchronized(commandQueue) {
            commandQueue.clear()
            isExecuting = false
        }

        Log.e("BLE", "========================================")
        Log.e("BLE", "Preparing GATT connection")
        Log.e("BLE", "Device name    = ${device.name}")
        Log.e("BLE", "Device address = ${device.address}")
        Log.e("BLE", "Target address = $deviceAddress")
        Log.e("BLE", "========================================")

        bluetoothGatt = device.connectGatt(
            context,
            false,
            gattCallback,
            BluetoothDevice.TRANSPORT_LE
        )

        Log.e(
            "BLE",
            "connectGatt() returned = ${bluetoothGatt != null}"
        )
    }

    // --- ROBUST QUEUE SYSTEM ---
    private fun enqueueCommand(command: Runnable) {
        synchronized(commandQueue) {
            commandQueue.add(command)
            if (!isExecuting) {
                executeNext()
            }
        }
    }

    private fun executeNext() {
        synchronized(commandQueue) {
            if (isExecuting) return
            val command = commandQueue.poll()
            if (command != null) {
                isExecuting = true
                handler.post {
                    // Create a specific timeout for this command execution
                    val timeout = Runnable {
                        Log.w("BLE", "[$deviceName] ⚠️ Command timed out! Advancing queue.")
                        commandCompleted()
                    }
                    currentTimeout = timeout
                    handler.postDelayed(timeout, 1000)

                    // Execute the command
                    command.run()
                }
            }
        }
    }

    private fun commandCompleted() {
        synchronized(commandQueue) {
            currentTimeout?.let { handler.removeCallbacks(it) }
            currentTimeout = null
            isExecuting = false
            executeNext()
        }
    }

    fun writeRawCommand(bytes: ByteArray) {
        if (bytes.isEmpty()) return
        Log.d(
            "BLE",
            "writeRawCommand: ${bytes.joinToString("") { "%02X".format(it) }}"
        )
        Log.d("BLE", "[$deviceName] ⬇️ Queuing command. Queue size: ${commandQueue.size}")

        enqueueCommand {
            // SILENT KILLER 1: Device disconnected in the background
            if (!isConnected()) {
                Log.e("BLE", "[$deviceName] ❌ DROPPED: Device is not connected!")
                commandCompleted()
                return@enqueueCommand
            }

            // SILENT KILLER 2: Initialization didn't finish properly
            val service = bluetoothGatt?.getService(SERVICE_UUID)
            if (service == null) {
                Log.e("BLE", "[$deviceName] ❌ DROPPED: Service not found! Did init finish?")
                commandCompleted()
                return@enqueueCommand
            }

            val char = service.getCharacteristic(WRITE_UUID)
            if (char == null) {
                Log.e("BLE", "[$deviceName] ❌ DROPPED: WRITE_UUID characteristic not found!")
                commandCompleted()
                return@enqueueCommand
            }

            char.value = bytes
            char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE

            // SILENT KILLER 3: Android BLE stack rejected the command
            val success = bluetoothGatt?.writeCharacteristic(char) ?: false
            if (!success) {
                Log.e("BLE", "[$deviceName] ❌ DROPPED: Android OS rejected writeCharacteristic()")
                commandCompleted()
            } else {
                Log.d("BLE", "[$deviceName] ✓ Write initiated to Bluetooth chip")
            }
        }
    }

    fun requestBattery() {
        when (deviceType) {

            DeviceType.BRACELET -> {
                val command = byteArrayOf(
                    0x0B.toByte(),
                    0x01.toByte(),
                    0x01.toByte()
                )

                writeRawCommand(command)

                Log.d("BLE", "[$deviceName] Bracelet battery request sent")
            }

            DeviceType.BELT -> {
                Log.d(
                    "BLE",
                    "[$deviceName] Belt battery uses power status notification"
                )
            }
        }
    }

    fun requestFirmware() {
        when (deviceType) {

            DeviceType.BRACELET -> {
                val command = byteArrayOf(
                    0x06.toByte(),
                    0x00.toByte()
                )

                writeRawCommand(command)

                Log.d(
                    "BLE",
                    "[$deviceName] Bracelet firmware request sent"
                )
            }

            DeviceType.BELT -> {
                enqueueCommand {
                    val service = bluetoothGatt?.getService(SERVICE_UUID)

                    if (service == null) {
                        Log.e("BLE", "[$deviceName] ❌ Belt service not found for firmware")
                        commandCompleted()
                        return@enqueueCommand
                    }

                    val char = service.getCharacteristic(BELT_FIRMWARE_UUID)

                    if (char == null) {
                        Log.e("BLE", "[$deviceName] ❌ Belt firmware FE01 not found")
                        commandCompleted()
                        return@enqueueCommand
                    }

                    val success =
                        bluetoothGatt?.readCharacteristic(char) ?: false

                    if (!success) {
                        Log.e("BLE", "[$deviceName] ❌ Read Belt firmware failed")
                        commandCompleted()
                    }
                }
            }
        }
    }

    private fun subscribeTo(uuid: UUID, name: String) {
        enqueueCommand {
            val service = bluetoothGatt?.getService(SERVICE_UUID)

            if (service == null) {
                Log.e(
                    "BLE",
                    "[$deviceName] ❌ Service not found for $name: $SERVICE_UUID"
                )
                commandCompleted()
                return@enqueueCommand
            }

            val char = service.getCharacteristic(uuid)

            if (char == null) {
                Log.e(
                    "BLE",
                    "[$deviceName] ❌ Characteristic not found for $name: $uuid"
                )
                commandCompleted()
                return@enqueueCommand
            }

            val properties = char.properties

            Log.d(
                "BLE",
                "[$deviceName] $name found: $uuid " +
                        "properties=$properties " +
                        "NOTIFY=${(properties and BluetoothGattCharacteristic.PROPERTY_NOTIFY) != 0} " +
                        "INDICATE=${(properties and BluetoothGattCharacteristic.PROPERTY_INDICATE) != 0}"
            )

            val localEnabled =
                bluetoothGatt?.setCharacteristicNotification(char, true) ?: false

            Log.d(
                "BLE",
                "[$deviceName] setCharacteristicNotification($name) = $localEnabled"
            )

            if (!localEnabled) {
                Log.e(
                    "BLE",
                    "[$deviceName] ❌ Local notification enable failed: $uuid"
                )
                commandCompleted()
                return@enqueueCommand
            }

            val desc = char.getDescriptor(CCCD_UUID)

            if (desc == null) {
                Log.e(
                    "BLE",
                    "[$deviceName] ❌ CCCD not found for $name: $uuid"
                )
                commandCompleted()
                return@enqueueCommand
            }

            Log.d(
                "BLE",
                "[$deviceName] CCCD found for $name: ${desc.uuid}"
            )

            desc.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE

            val descriptorWrite =
                bluetoothGatt?.writeDescriptor(desc) ?: false

            Log.d(
                "BLE",
                "[$deviceName] writeDescriptor($name) = $descriptorWrite"
            )

            if (!descriptorWrite) {
                Log.e(
                    "BLE",
                    "[$deviceName] ❌ CCCD write failed for $name"
                )
                commandCompleted()
            }
        }
    }

    private fun writeParam(bytes: ByteArray, name: String) {
        enqueueCommand {
            val char = bluetoothGatt?.getService(SERVICE_UUID)?.getCharacteristic(PARAM_UUID)
            if (char != null) {
                char.value = bytes
                char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                if (bluetoothGatt?.writeCharacteristic(char) == false) {
                    commandCompleted()
                }
            } else {
                commandCompleted()
            }
        }
    }

    private val gattCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(
            gatt: BluetoothGatt,
            status: Int,
            newState: Int
        ) {
            // Abaikan callback dari koneksi GATT lama
            if (bluetoothGatt !== gatt) {
                Log.w(
                    "BLE",
                    "[$deviceName] ⚠️ Ignoring stale GATT callback " +
                            "from ${gatt.device.address}"
                )
                return
            }

            Log.e("BLE", "========================================")
            Log.e("BLE", "[$deviceName] onConnectionStateChange()")
            Log.e("BLE", "[$deviceName] address = ${gatt.device.address}")
            Log.e("BLE", "[$deviceName] status = $status")
            Log.e("BLE", "[$deviceName] newState = $newState")
            Log.e("BLE", "========================================")

            connectionState = newState

            when (newState) {

                BluetoothProfile.STATE_CONNECTED -> {
                    Log.e("BLE", "[$deviceName] >>> CONNECTED <<<")

                    handler.postDelayed({

                        // Pastikan GATT yang sama masih aktif
                        if (bluetoothGatt !== gatt) {
                            Log.w(
                                "BLE",
                                "[$deviceName] ⚠️ Skip service discovery: stale GATT"
                            )
                            return@postDelayed
                        }

                        if (connectionState != BluetoothProfile.STATE_CONNECTED) {
                            Log.w(
                                "BLE",
                                "[$deviceName] ⚠️ Skip service discovery: no longer connected"
                            )
                            return@postDelayed
                        }

                        Log.e(
                            "BLE",
                            "[$deviceName] Starting service discovery..."
                        )

                        val result = gatt.discoverServices()

                        Log.e(
                            "BLE",
                            "[$deviceName] discoverServices() returned = $result"
                        )

                    }, 1000)
                }

                BluetoothProfile.STATE_DISCONNECTED -> {
                    Log.e(
                        "BLE",
                        "[$deviceName] >>> DISCONNECTED <<< status=$status"
                    )

                    synchronized(commandQueue) {
                        commandQueue.clear()
                        isExecuting = false
                    }

                    gatt.close()

                    // Hanya kosongkan current GATT jika memang ini GATT aktif
                    if (bluetoothGatt === gatt) {
                        bluetoothGatt = null
                    }
                }

                BluetoothProfile.STATE_CONNECTING -> {
                    Log.e(
                        "BLE",
                        "[$deviceName] >>> CONNECTING <<<"
                    )
                }

                BluetoothProfile.STATE_DISCONNECTING -> {
                    Log.e(
                        "BLE",
                        "[$deviceName] >>> DISCONNECTING <<<"
                    )
                }

                else -> {
                    Log.e(
                        "BLE",
                        "[$deviceName] >>> UNKNOWN STATE: $newState <<<"
                    )
                }
            }
        }

        override fun onServicesDiscovered(
            gatt: BluetoothGatt,
            status: Int
        ) {
            super.onServicesDiscovered(gatt, status)

            if (status != BluetoothGatt.GATT_SUCCESS) {
                Log.e("BLE", "[$deviceName] Service discovery failed: $status")
                return
            }

            Log.i("BLE", "[$deviceName] Services discovered")

            when (deviceType) {

                DeviceType.BELT -> {
                    Log.i("BLE", "[$deviceName] Using Belt service FE51")

                    subscribeTo(
                        UUID.fromString("0000fe09-0000-1000-8000-00805f9b34fb"),
                        "Belt Battery FE09"
                    )
                }

                DeviceType.BRACELET -> {
                    Log.i("BLE", "[$deviceName] Using Bracelet service FE55")

                    subscribeTo(
                        UUID.fromString("0000fe02-0000-1000-8000-00805f9b34fb"),
                        "Bracelet Keep Alive"
                    )

                    subscribeTo(
                        UUID.fromString("0000fe16-0000-1000-8000-00805f9b34fb"),
                        "Bracelet Notify"
                    )

                    val stopBytes = byteArrayOf(
                        0x03,
                        0x01,
                        0xFF.toByte()
                    )

                    writeRawCommand(stopBytes)

                    restoreBraceletIntensity()
                    restoreBraceletPattern()
                }
            }
        }

        override fun onDescriptorWrite(
            gatt: BluetoothGatt,
            descriptor: BluetoothGattDescriptor,
            status: Int
        ) {
            Log.d(
                "BLE",
                "[$deviceName] onDescriptorWrite: " +
                        "uuid=${descriptor.uuid}, " +
                        "char=${descriptor.characteristic.uuid}, " +
                        "status=$status"
            )

            if (status == BluetoothGatt.GATT_SUCCESS) {
                Log.d(
                    "BLE",
                    "[$deviceName] ✅ CCCD write SUCCESS"
                )
            } else {
                Log.e(
                    "BLE",
                    "[$deviceName] ❌ CCCD write FAILED status=$status"
                )
            }

            commandCompleted()
        }

        override fun onCharacteristicWrite(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic, status: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                Log.e("BLE", "[$deviceName] ❌ Write failed! Status: $status")
            }
            commandCompleted()
        }

        @Deprecated("Deprecated in Java")
        override fun onCharacteristicChanged(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic
        ) {
            val data = characteristic.value

            Log.d(
                "BLE",
                "[$deviceName] NOTIFICATION ${characteristic.uuid}: ${
                    data.joinToString(" ") { "%02X".format(it) }
                }"
            )

            when (deviceType) {

                DeviceType.BRACELET -> {

                    // Bracelet FE02 = Keep Alive
                    if (characteristic.uuid == KEEP_ALIVE_UUID) {
                        enqueueCommand {
                            val char = gatt
                                .getService(SERVICE_UUID)
                                ?.getCharacteristic(KEEP_ALIVE_UUID)

                            if (char != null) {
                                char.value = byteArrayOf(0x01)
                                char.writeType =
                                    BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE

                                if (bluetoothGatt?.writeCharacteristic(char) == false) {
                                    commandCompleted()
                                }
                            } else {
                                commandCompleted()
                            }
                        }
                    }

                    // Bracelet FE16 = notification
                    if (characteristic.uuid == NOTIFY_UUID) {
                        parseBraceletNotification(data)
                    }
                }

                DeviceType.BELT -> {

                    // Belt FE06 = notification
                    if (characteristic.uuid == NOTIFY_UUID) {
                        parseBeltNotification(data)
                    }
                }
            }
        }
        @Deprecated("Deprecated in Java")
        override fun onCharacteristicRead(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            status: Int
        ) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                Log.e(
                    "BLE",
                    "[$deviceName] Characteristic read failed: " +
                            "uuid=${characteristic.uuid}, status=$status"
                )
                commandCompleted()
                return
            }

            val data = characteristic.value

            Log.d(
                "BLE",
                "[$deviceName] READ ${characteristic.uuid}: ${
                    data.joinToString(" ") { "%02X".format(it) }
                }"
            )

            if (
                deviceType == DeviceType.BELT &&
                characteristic.uuid == BELT_FIRMWARE_UUID
            ) {
                parseBeltFirmware(data)
            }

            commandCompleted()
        }
    }

    private fun parseBeltFirmware(data: ByteArray) {
        if (data.size < 2) {
            Log.w(
                "BLE",
                "[$deviceName] Belt firmware data too short: ${data.size}"
            )
            return
        }

        val firmwareVersion =
            (data[0].toInt() and 0xFF) or
                    ((data[1].toInt() and 0xFF) shl 8)

        Log.d(
            "BLE",
            "[$deviceName] Belt Firmware Version: $firmwareVersion"
        )

        handler.post {
            informationListener?.onFirmwareUpdate(
                firmwareVersion.toString()
            )
        }
    }

    private fun parseBeltNotification(data: ByteArray) {
        if (data.size < 9) {
            Log.w(
                "BLE",
                "[$deviceName] Belt battery packet too short: ${data.size}"
            )
            return
        }

        val batteryStatus =
            data[0].toInt() and 0xFF

        val chargeRaw =
            (data[1].toInt() and 0xFF) or
                    ((data[2].toInt() and 0xFF) shl 8)

        var chargeLevel =
            chargeRaw / 256.0f

        if (chargeLevel > 100f) {
            chargeLevel = 100f
        }

        val ttfeRaw =
            (data[3].toInt() and 0xFF) or
                    ((data[4].toInt() and 0xFF) shl 8)

        val ttfe =
            ttfeRaw * 5.625f

        val currentRaw =
            (data[5].toInt() and 0xFF) or
                    ((data[6].toInt() and 0xFF) shl 8)

        val currentMa =
            currentRaw.toShort().toInt()

        val voltageMv =
            (data[7].toInt() and 0xFF) or
                    ((data[8].toInt() and 0xFF) shl 8)

        Log.d(
            "BLE",
            "[$deviceName] Belt Battery: " +
                    "level=${"%.2f".format(chargeLevel)}%, " +
                    "status=$batteryStatus, " +
                    "ttfe=${"%.1f".format(ttfe)} ms, " +
                    "current=${currentMa} mA, " +
                    "voltage=${voltageMv} mV"
        )

        handler.post {
            informationListener?.onBatteryUpdate(
                batteryStatus,
                chargeLevel
            )
        }
    }
    private fun parseBraceletNotification(data: ByteArray) {

        // Battery:
        // 8B 02 [charge_state] [battery_level]
        if (data.size >= 4 &&
            data[0].toInt() and 0xFF == 0x8B
        ) {
            val chargeState = data[2].toInt() and 0xFF
            val batteryLevel = data[3].toInt() and 0xFF

            Log.d(
                "BLE",
                "[$deviceName] Bracelet Battery: $batteryLevel%, state=$chargeState"
            )

            handler.post {
                informationListener?.onBatteryUpdate(
                    chargeState,
                    batteryLevel.toFloat()
                )
            }
        }

        // Firmware:
        // 86 04 [variant] [major] [minor] [protocol]
        if (data.size >= 6 &&
            data[0].toInt() and 0xFF == 0x86
        ) {
            val major = data[3].toInt() and 0xFF
            val minor = data[4].toInt() and 0xFF
            val protocol = data[5].toInt() and 0xFF

            Log.d(
                "BLE",
                "[$deviceName] Bracelet Firmware: $major.$minor.$protocol"
            )

            handler.post {
                informationListener?.onFirmwareUpdate(
                    "$major.$minor.$protocol"
                )
            }
        }
    }
    private fun restoreBraceletIntensity() {
        val prefs = context.getSharedPreferences(
            "FullIntensityPrefs",
            Context.MODE_PRIVATE
        )

        val left = prefs.getInt("left", 50)
        val down = prefs.getInt("down", 50)
        val right = prefs.getInt("right", 50)
        val top = prefs.getInt("top", 50)
        val topFront = prefs.getInt("topFront", 50)
        val topBack = prefs.getInt("topBack", 50)

        val command = byteArrayOf(
            0x02.toByte(),
            0x06.toByte(),
            left.toByte(),
            down.toByte(),
            right.toByte(),
            top.toByte(),
            topFront.toByte(),
            topBack.toByte()
        )

        Log.d(
            "BLE",
            "[$deviceName] 🔄 Restoring intensity: " +
                    "L=$left D=$down R=$right T=$top TF=$topFront TB=$topBack"
        )

        writeRawCommand(command)
    }

    private fun restoreBraceletPattern() {
        val prefs = context.getSharedPreferences(
            "PatternPrefs",
            Context.MODE_PRIVATE
        )

        val pattern = prefs.getInt("PATTERN_INDEX", 0)

        val intensityPrefs = context.getSharedPreferences(
            "FullIntensityPrefs",
            Context.MODE_PRIVATE
        )

        val left = intensityPrefs.getInt("left", 50)
        val down = intensityPrefs.getInt("down", 50)
        val right = intensityPrefs.getInt("right", 50)
        val top = intensityPrefs.getInt("top", 50)
        val topFront = intensityPrefs.getInt("topFront", 50)
        val topBack = intensityPrefs.getInt("topBack", 50)

        val previewCommand = byteArrayOf(
            0x0F.toByte(),
            0x07.toByte(),
            pattern.toByte(),
            left.toByte(),
            down.toByte(),
            right.toByte(),
            top.toByte(),
            topFront.toByte(),
            topBack.toByte()
        )

        if (isConnected()) {
            writeRawCommand(previewCommand)

            Log.d(
                "BLE",
                "[$deviceName] Restoring pattern preview: pattern=$pattern"
            )

            val previewDuration = when (pattern) {
                0 -> 600L   // SINGLE
                1 -> 600L   // MULTI
                2 -> 1200L  // SEQ
                else -> 600L
            }

            handler.postDelayed({
                val stopCommand = byteArrayOf(
                    0x03.toByte(),
                    0x01.toByte(),
                    0xFF.toByte()
                )

                if (isConnected()) {
                    writeRawCommand(stopCommand)

                    Log.d(
                        "BLE",
                        "[$deviceName] Pattern feedback stopped after 600ms"
                    )
                }
            }, previewDuration)
        }
    }

    fun testVibration() {
        val testCommand = byteArrayOf(
            0x01, 0x00, 60.toByte(), 0x00, 0x00, 0x00, 0x00,
            0x00.toByte(), 0x00.toByte(), 0x00, 0x00, 0x00,
            0x64.toByte(), 0x00, 0x00, 0x00, 0x00, 0x00
        )
        writeRawCommand(testCommand)
        Log.i("BLE", "[$deviceName] Test vibration sent!")
    }

    fun disconnect() {
        synchronized(commandQueue) {
            commandQueue.clear()
        }
        bluetoothGatt?.disconnect()
        bluetoothGatt?.close()
        bluetoothGatt = null
        connectionState = BluetoothProfile.STATE_DISCONNECTED
    }

    fun isConnected(): Boolean {
        Log.d(
            "BLE",
            "[$deviceName] isConnected() = ${connectionState == BluetoothProfile.STATE_CONNECTED}, state=$connectionState"
        )
        return connectionState == BluetoothProfile.STATE_CONNECTED
    }
}