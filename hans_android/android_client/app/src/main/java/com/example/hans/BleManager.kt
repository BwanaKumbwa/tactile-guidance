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
            DeviceType.BELT     -> UUID.fromString("0000fe51-0000-1000-8000-00805f9b34fb")
            DeviceType.BRACELET -> UUID.fromString("0000fe55-0000-1000-8000-00805f9b34fb")
        }

    private val KEEP_ALIVE_UUID: UUID
        get() = when (deviceType) {
            DeviceType.BELT     -> UUID.fromString("0000fe02-0000-1000-8000-00805f9b34fb")
            DeviceType.BRACELET -> UUID.fromString("0000fe02-0000-1000-8000-00805f9b34fb")
        }

    private val WRITE_UUID: UUID
        get() = when (deviceType) {
            DeviceType.BELT     -> UUID.fromString("0000fe03-0000-1000-8000-00805f9b34fb")
            DeviceType.BRACELET -> UUID.fromString("0000fe15-0000-1000-8000-00805f9b34fb")
        }

    // Belt specific characteristics
    private val BELT_PARAM_WRITE_UUID  = UUID.fromString("0000fe05-0000-1000-8000-00805f9b34fb")
    private val BELT_PARAM_NOTIFY_UUID = UUID.fromString("0000fe06-0000-1000-8000-00805f9b34fb")
    private val BELT_BATTERY_NOTIFY_UUID = UUID.fromString("0000fe09-0000-1000-8000-00805f9b34fb")
    private val BELT_FIRMWARE_UUID     = UUID.fromString("0000fe01-0000-1000-8000-00805f9b34fb")

    // Bracelet notify characteristic
    private val BRACELET_NOTIFY_UUID   = UUID.fromString("0000fe16-0000-1000-8000-00805f9b34fb")

    private val CCCD_UUID              = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

    // Robust Command Queue
    private val commandQueue: Queue<Runnable> = LinkedList()
    @Volatile private var isExecuting = false
    private var deviceName = "Unknown"
    private var currentTimeout: Runnable? = null

    interface InformationListener {
        fun onBatteryUpdate(chargeState: Int, batteryLevel: Float)
        fun onFirmwareUpdate(version: String)
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
        Log.i("BLE", "[$deviceName] Connecting ($deviceAddress)...")

        synchronized(commandQueue) {
            commandQueue.clear()
            isExecuting = false
        }

        bluetoothGatt = device.connectGatt(
            context,
            false,
            gattCallback,
            BluetoothDevice.TRANSPORT_LE
        )
    }

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
                    val timeout = Runnable {
                        Log.w("BLE", "[$deviceName] ⚠️ Command timed out! Advancing queue.")
                        commandCompleted()
                    }
                    currentTimeout = timeout
                    handler.postDelayed(timeout, 800)

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

        enqueueCommand {
            if (!isConnected()) {
                commandCompleted()
                return@enqueueCommand
            }

            val service = bluetoothGatt?.getService(SERVICE_UUID)
            if (service == null) {
                commandCompleted()
                return@enqueueCommand
            }

            val char = service.getCharacteristic(WRITE_UUID)
            if (char == null) {
                commandCompleted()
                return@enqueueCommand
            }

            char.value = bytes
            char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE

            val success = bluetoothGatt?.writeCharacteristic(char) ?: false
            if (!success) {
                Log.e("BLE", "[$deviceName] OS rejected writeCharacteristic()")
            }
            commandCompleted()
        }
    }

    private fun writeParam(bytes: ByteArray, name: String) {
        enqueueCommand {
            if (!isConnected()) {
                commandCompleted()
                return@enqueueCommand
            }

            val char = bluetoothGatt?.getService(SERVICE_UUID)?.getCharacteristic(BELT_PARAM_WRITE_UUID)
            if (char != null) {
                char.value = bytes
                char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                val success = bluetoothGatt?.writeCharacteristic(char) ?: false
                if (!success) {
                    commandCompleted()
                }
            } else {
                commandCompleted()
            }
        }
    }

    fun requestBattery() {
        when (deviceType) {
            DeviceType.BRACELET -> {
                val command = byteArrayOf(0x0B.toByte(), 0x01.toByte(), 0x01.toByte())
                writeRawCommand(command)
            }
            DeviceType.BELT -> {
                // Belt pushes battery via FE09 notifications automatically once subscribed
            }
        }
    }

    fun requestFirmware() {
        when (deviceType) {
            DeviceType.BRACELET -> {
                val command = byteArrayOf(0x06.toByte(), 0x00.toByte())
                writeRawCommand(command)
            }
            DeviceType.BELT -> {
                enqueueCommand {
                    val char = bluetoothGatt?.getService(SERVICE_UUID)?.getCharacteristic(BELT_FIRMWARE_UUID)
                    if (char != null) {
                        val success = bluetoothGatt?.readCharacteristic(char) ?: false
                        if (!success) commandCompleted()
                    } else {
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
                commandCompleted()
                return@enqueueCommand
            }

            val char = service.getCharacteristic(uuid)
            if (char == null) {
                commandCompleted()
                return@enqueueCommand
            }

            val localEnabled = bluetoothGatt?.setCharacteristicNotification(char, true) ?: false
            if (!localEnabled) {
                commandCompleted()
                return@enqueueCommand
            }

            val desc = char.getDescriptor(CCCD_UUID)
            if (desc == null) {
                commandCompleted()
                return@enqueueCommand
            }

            desc.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
            val descriptorWrite = bluetoothGatt?.writeDescriptor(desc) ?: false
            if (!descriptorWrite) {
                commandCompleted()
            }
        }
    }

    private val gattCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            if (bluetoothGatt !== gatt) return
            connectionState = newState

            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    Log.i("BLE", "[$deviceName] >>> CONNECTED <<<")
                    handler.postDelayed({
                        if (bluetoothGatt === gatt && connectionState == BluetoothProfile.STATE_CONNECTED) {
                            gatt.discoverServices()
                        }
                    }, 800)
                }

                BluetoothProfile.STATE_DISCONNECTED -> {
                    Log.w("BLE", "[$deviceName] >>> DISCONNECTED <<< status=$status")
                    synchronized(commandQueue) {
                        commandQueue.clear()
                        isExecuting = false
                    }
                    gatt.close()
                    if (bluetoothGatt === gatt) {
                        bluetoothGatt = null
                    }
                }
            }
        }

        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
            super.onServicesDiscovered(gatt, status)
            if (status != BluetoothGatt.GATT_SUCCESS) return

            Log.i("BLE", "[$deviceName] Services discovered")

            when (deviceType) {
                DeviceType.BELT -> {
                    Log.i("BLE", "[$deviceName] Initializing FeelSpace Belt (FE51)...")

                    // 1. Subscribe to Keep-Alive (FE02)
                    subscribeTo(KEEP_ALIVE_UUID, "Belt Keep Alive (FE02)")

                    // 2. Subscribe to Param Notify (FE06)
                    subscribeTo(BELT_PARAM_NOTIFY_UUID, "Belt Param Notify (FE06)")

                    // 3. CRITICAL: Subscribe to Battery Notifications (FE09)
                    subscribeTo(BELT_BATTERY_NOTIFY_UUID, "Belt Battery (FE09)")

                    // 4. Switch to APP MODE (Disables standby compass hum)
                    writeParam(byteArrayOf(0x01, 0x81.toByte(), 0x03), "Set Belt to APP MODE")

                    // 5. Clear any residual vibration
                    val stopBytes = byteArrayOf(0x30.toByte(), 0xFF.toByte())
                    writeRawCommand(stopBytes)
                }

                DeviceType.BRACELET -> {
                    Log.i("BLE", "[$deviceName] Initializing Bracelet (FE55)...")
                    subscribeTo(KEEP_ALIVE_UUID, "Bracelet Keep Alive")
                    subscribeTo(BRACELET_NOTIFY_UUID, "Bracelet Notify")

                    val stopBytes = byteArrayOf(0x03, 0x01, 0xFF.toByte())
                    writeRawCommand(stopBytes)

                    restoreBraceletIntensity()
                    restoreBraceletPattern()
                    requestBattery()
                }
            }
        }

        override fun onDescriptorWrite(
            gatt: BluetoothGatt,
            descriptor: BluetoothGattDescriptor,
            status: Int
        ) {
            commandCompleted()
        }

        override fun onCharacteristicWrite(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            status: Int
        ) {
            commandCompleted()
        }

        @Deprecated("Deprecated in Java")
        override fun onCharacteristicChanged(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic
        ) {
            val data = characteristic.value

            // Respond immediately to KEEP_ALIVE without queueing
            if (characteristic.uuid == KEEP_ALIVE_UUID) {
                val char = gatt.getService(SERVICE_UUID)?.getCharacteristic(KEEP_ALIVE_UUID)
                if (char != null) {
                    char.value = byteArrayOf(0x01)
                    char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
                    bluetoothGatt?.writeCharacteristic(char)
                }
                return
            }

            when (deviceType) {
                DeviceType.BRACELET -> {
                    if (characteristic.uuid == BRACELET_NOTIFY_UUID) {
                        parseBraceletNotification(data)
                    }
                }
                DeviceType.BELT -> {
                    if (characteristic.uuid == BELT_BATTERY_NOTIFY_UUID) {
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
            if (status == BluetoothGatt.GATT_SUCCESS) {
                val data = characteristic.value
                if (deviceType == DeviceType.BELT) {
                    when (characteristic.uuid) {
                        BELT_FIRMWARE_UUID -> parseBeltFirmware(data)
                        BELT_BATTERY_NOTIFY_UUID -> parseBeltNotification(data)
                    }
                }
            }
            commandCompleted()
        }
    }

    private fun parseBeltFirmware(data: ByteArray) {
        if (data.size < 2) return
        val firmwareVersion = (data[0].toInt() and 0xFF) or ((data[1].toInt() and 0xFF) shl 8)
        handler.post { informationListener?.onFirmwareUpdate(firmwareVersion.toString()) }
    }

    private fun parseBeltNotification(data: ByteArray) {
        if (data.isEmpty()) return

        val rawStatus = data[0].toInt() and 0xFF

        // FeelSpace naviBelt FE09 packet:
        // Byte 0: Power/Charge status
        // Byte 1: Charge LSB, Byte 2: Charge MSB (chargeRaw / 256.0f = Percentage)
        val chargeLevel = if (data.size >= 3) {
            val chargeRaw = (data[1].toInt() and 0xFF) or ((data[2].toInt() and 0xFF) shl 8)
            (chargeRaw / 256.0f).coerceIn(0f, 100f)
        } else if (data.size >= 2) {
            (data[1].toInt() and 0xFF).toFloat().coerceIn(0f, 100f)
        } else {
            rawStatus.toFloat().coerceIn(0f, 100f)
        }

        // Status normalization:
        // If 0, belt is actively discharging on battery -> map to 1 (Discharging) so UI doesn't say "Connecting"
        // If 2 -> Charging, If 3 -> Full
        val normalizedStatus = when (rawStatus) {
            0 -> 1 // Discharging / In use
            else -> rawStatus
        }

        handler.post {
            informationListener?.onBatteryUpdate(normalizedStatus, chargeLevel)
        }
    }

    private fun parseBraceletNotification(data: ByteArray) {
        if (data.size >= 4 && (data[0].toInt() and 0xFF) == 0x8B) {
            val chargeState = data[2].toInt() and 0xFF
            val batteryLevel = data[3].toInt() and 0xFF
            handler.post { informationListener?.onBatteryUpdate(chargeState, batteryLevel.toFloat()) }
        }

        if (data.size >= 6 && (data[0].toInt() and 0xFF) == 0x86) {
            val major = data[3].toInt() and 0xFF
            val minor = data[4].toInt() and 0xFF
            val protocol = data[5].toInt() and 0xFF
            handler.post { informationListener?.onFirmwareUpdate("$major.$minor.$protocol") }
        }
    }

    private fun restoreBraceletIntensity() {
        val prefs = context.getSharedPreferences("FullIntensityPrefs", Context.MODE_PRIVATE)
        val left = prefs.getInt("left", 50)
        val down = prefs.getInt("down", 50)
        val right = prefs.getInt("right", 50)
        val top = prefs.getInt("top", 50)
        val topFront = prefs.getInt("topFront", 50)
        val topBack = prefs.getInt("topBack", 50)

        val command = byteArrayOf(
            0x02.toByte(), 0x06.toByte(),
            left.toByte(), down.toByte(), right.toByte(), top.toByte(), topFront.toByte(), topBack.toByte()
        )
        writeRawCommand(command)
    }

    private fun restoreBraceletPattern() {
        val prefs = context.getSharedPreferences("PatternPrefs", Context.MODE_PRIVATE)
        val pattern = prefs.getInt("PATTERN_INDEX", 0)
        val intensityPrefs = context.getSharedPreferences("FullIntensityPrefs", Context.MODE_PRIVATE)

        val left = intensityPrefs.getInt("left", 50)
        val down = intensityPrefs.getInt("down", 50)
        val right = intensityPrefs.getInt("right", 50)
        val top = intensityPrefs.getInt("top", 50)
        val topFront = intensityPrefs.getInt("topFront", 50)
        val topBack = intensityPrefs.getInt("topBack", 50)

        val previewCommand = byteArrayOf(
            0x0F.toByte(), 0x07.toByte(), pattern.toByte(),
            left.toByte(), down.toByte(), right.toByte(), top.toByte(), topFront.toByte(), topBack.toByte()
        )

        if (isConnected()) {
            writeRawCommand(previewCommand)
            handler.postDelayed({
                val stopCommand = byteArrayOf(0x03.toByte(), 0x01.toByte(), 0xFF.toByte())
                if (isConnected()) writeRawCommand(stopCommand)
            }, 600L)
        }
    }

    fun disconnect() {
        synchronized(commandQueue) { commandQueue.clear() }
        bluetoothGatt?.disconnect()
        bluetoothGatt?.close()
        bluetoothGatt = null
        connectionState = BluetoothProfile.STATE_DISCONNECTED
    }

    fun isConnected(): Boolean {
        return connectionState == BluetoothProfile.STATE_CONNECTED
    }
}
