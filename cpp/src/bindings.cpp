#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "ladderbook/book.hpp"

#include "ladderbook/fees.hpp"
#include "ladderbook/sim.hpp"

namespace py = pybind11;
using namespace ladderbook;

PYBIND11_MODULE(_core, m) {
    m.doc() = "ladderbook C++ core: fixed-point fees, order book and replay engine";
    m.attr("PRICE_ONE") = kPriceOne;
    m.attr("QTY_ONE") = kQtyOne;
    m.attr("TAKER_RATE") = kTakerRate;
    m.attr("MAKER_RATE") = kMakerRate;

    m.def("trade_fee_micros", &trade_fee_micros, py::arg("rate"), py::arg("multiplier"), py::arg("price"),
          py::arg("qty"), "Kalshi trade fee in micro-dollars (exact integer arithmetic).");

    py::class_<FillCash>(m, "FillCash")
        .def_readonly("balance_change", &FillCash::balance_change)
        .def_readonly("trade_fee", &FillCash::trade_fee)
        .def_readonly("rounding_fee", &FillCash::rounding_fee)
        .def_readonly("rebate", &FillCash::rebate);

    py::class_<OrderFeeAccumulator>(m, "OrderFeeAccumulator")
        .def(py::init<Micros>(), py::arg("precision") = 10'000)
        .def("apply", &OrderFeeAccumulator::apply, py::arg("revenue"), py::arg("trade_fee"))
        .def_property_readonly("carried", &OrderFeeAccumulator::carried);

    py::enum_<Side>(m, "Side").value("BID", Side::Bid).value("ASK", Side::Ask);

    py::class_<BinaryBook>(m, "BinaryBook")
        .def(py::init<>())
        .def("clear", &BinaryBook::clear)
        .def("set_level", &BinaryBook::set_level, py::arg("side"), py::arg("price"), py::arg("qty"))
        .def("apply_delta", &BinaryBook::apply_delta, py::arg("side"), py::arg("price"), py::arg("delta"))
        .def("best_bid", &BinaryBook::best_bid)
        .def("best_ask", &BinaryBook::best_ask)
        .def("depth_at", &BinaryBook::depth_at, py::arg("side"), py::arg("price"))
        .def("levels", &BinaryBook::levels, py::arg("side"))
        .def("crossed", &BinaryBook::crossed);

    py::enum_<QueueModel>(m, "QueueModel")
        .value("CONSERVATIVE", QueueModel::Conservative)
        .value("PROPORTIONAL", QueueModel::Proportional)
        .value("OPTIMISTIC", QueueModel::Optimistic);
    py::enum_<OrderType>(m, "OrderType")
        .value("POST_ONLY", OrderType::PostOnly)
        .value("IOC", OrderType::ImmediateOrCancel);
    py::enum_<OrderStatus>(m, "OrderStatus")
        .value("PENDING", OrderStatus::Pending)
        .value("RESTING", OrderStatus::Resting)
        .value("FILLED", OrderStatus::Filled)
        .value("CANCELLED", OrderStatus::Cancelled)
        .value("REJECTED", OrderStatus::Rejected);

    py::class_<SimConfig>(m, "SimConfig")
        .def(py::init<>())
        .def_readwrite("order_latency", &SimConfig::order_latency)
        .def_readwrite("cancel_latency", &SimConfig::cancel_latency)
        .def_readwrite("queue_model", &SimConfig::queue_model)
        .def_readwrite("maker_rate", &SimConfig::maker_rate)
        .def_readwrite("taker_rate", &SimConfig::taker_rate)
        .def_readwrite("fee_multiplier", &SimConfig::fee_multiplier)
        .def_readwrite("balance_precision", &SimConfig::balance_precision);

    py::class_<Fill>(m, "Fill")
        .def_readonly("ts", &Fill::ts)
        .def_readonly("order_id", &Fill::order_id)
        .def_readonly("side", &Fill::side)
        .def_readonly("price", &Fill::price)
        .def_readonly("qty", &Fill::qty)
        .def_readonly("maker", &Fill::maker)
        .def_readonly("fee", &Fill::fee)
        .def_readonly("balance_change", &Fill::balance_change)
        .def("__repr__", [](const Fill& f) {
            return "<Fill " + std::string(f.side == Side::Bid ? "buy " : "sell ") + std::to_string(f.qty) + " @ " +
                   std::to_string(f.price) + ">";
        });

    py::class_<OrderState>(m, "OrderState")
        .def_readonly("id", &OrderState::id)
        .def_readonly("side", &OrderState::side)
        .def_readonly("price", &OrderState::price)
        .def_readonly("qty", &OrderState::qty)
        .def_readonly("remaining", &OrderState::remaining)
        .def_readonly("status", &OrderState::status)
        .def_readonly("queue_ahead", &OrderState::queue_ahead);

    py::class_<SimExchange>(m, "SimExchange")
        .def(py::init<SimConfig>(), py::arg("config") = SimConfig{})
        .def("on_clear", &SimExchange::on_clear, py::arg("ts"))
        .def("on_level", &SimExchange::on_level, py::arg("ts"), py::arg("side"), py::arg("price"), py::arg("qty"))
        .def("on_delta", &SimExchange::on_delta, py::arg("ts"), py::arg("side"), py::arg("price"), py::arg("delta"))
        .def("on_trade", &SimExchange::on_trade, py::arg("ts"), py::arg("price"), py::arg("qty"), py::arg("aggressor"))
        .def("place", &SimExchange::place, py::arg("ts"), py::arg("side"), py::arg("price"), py::arg("qty"),
             py::arg("type") = OrderType::PostOnly)
        .def("cancel", &SimExchange::cancel, py::arg("ts"), py::arg("order_id"))
        .def("advance", &SimExchange::advance, py::arg("ts"))
        .def("settle", &SimExchange::settle, py::arg("ts"), py::arg("yes"))
        .def("take_fills", &SimExchange::take_fills)
        .def("order", &SimExchange::order, py::arg("order_id"))
        .def("open_orders", &SimExchange::open_orders)
        .def_property_readonly("book", &SimExchange::book, py::return_value_policy::reference_internal)
        .def_property_readonly("position", &SimExchange::position)
        .def_property_readonly("cash", &SimExchange::cash)
        .def_property_readonly("fees_paid", &SimExchange::fees_paid);
}
