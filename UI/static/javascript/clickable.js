 /*
    Author: Yumei Li
    Date: 12/23/2017
    Requirement: jquery-3.2.1
    This file makes the elements in the file clickable
*/

function get_response(clicked_object) {
    var mode_info = clicked_object.getAttribute("mode");
    var id1 = clicked_object.children[0].id;
    var id2 = clicked_object.children[2].id;

    return {
        "id1": id1,
        "id2": id2,
        "mode": mode_info
    };
}

function get_cell_ajax(current_cell) {
    $.fn.extend({
        animateCss: function (animationName, callback) {
            var animationEnd = 'webkitAnimationEnd mozAnimationEnd MSAnimationEnd oanimationend animationend';
            this.addClass('animated ' + animationName).one(animationEnd, function() {
                $(this).removeClass('animated ' + animationName);
                if (callback) {
                  callback();
                }
            });
            return this;
        }
    });

    $.getJSON($SCRIPT_ROOT + '/get_cell', get_response(current_cell), function(data) {
        if(data.value1 && data.value2 && data.mode) {
            current_cell.classList.add("animated");
            current_cell.classList.add("fadeIn");
            window.setTimeout( function(){
                current_cell.classList.remove("animated");
                current_cell.classList.remove("fadeIn");
            }, 200);

            current_cell.children[0].innerHTML = data.value1;
            current_cell.children[2].innerHTML = data.value2;
            current_cell.setAttribute("mode", data.mode);
            if(data.mode == "full") {
                current_cell.classList.remove("clickable_cell");
            }
            
            var bar_style = 'width:' + data.cdp + '%';
            $("#character-disclosed-bar").attr("style", bar_style);
            $("#character-disclosed-value").html(data.cdp + "%")

            var bar_style2 = 'width:' + data.KAPR + '%';
            $("#privacy-risk-bar").attr("style", bar_style2);
            $("#privacy-risk-value").html(data.KAPR + "%")
            
            $("#privacy-risk-delta").attr("style", 'width: 0%');
            $("#privacy-risk-delta-value").html(" ")
            $("#character-percentage-delta").attr("style", 'width: 0%');
            $("#character-percentage-delta-value").html(" ");

            for(var i = 0; i < 6; i+=1) {
                var id = data.new_delta[i][0];
                var new_delta_value = data.new_delta[i][1];
                $DELTA[id] = new_delta_value;

                id = data.new_delta_cdp[i][0]
                var new_delta_cdp_value = data.new_delta_cdp[i][1];
                $DELTA_CDP[id] = new_delta_cdp_value;
            }
        }
    });
}

// mirror: this function has a mirror at form_submit.js
function make_cell_clickable() {
    // mark the double missing cell as unclickable
    $('.clickable_cell').each(function() {
        if( this.children[0].innerHTML.indexOf('missing') != -1 && this.children[2].innerHTML.indexOf('missing') != -1 ) {
            this.classList.remove("clickable_cell");
        }
    });

    // bind the clickable cell to ajax openning cell action
    $('.clickable_cell').bind('click', function() {
        var current_cell = this;
        if(current_cell.getAttribute("mode") != "full") {
            get_cell_ajax(current_cell);
        }

        // save the user click data
        $this_click = "user click: " + current_cell.children[0].id;
        var dt = new Date();
        $click_time = "click time: " + dt.getHours() + "h" + dt.getMinutes() + "m" + dt.getSeconds() + "s";
        $click_timestamp = "click timestamp: " + dt.getTime();
        $data = [$this_click, $click_time, $click_timestamp].join()
        $user_data += $data + ";";
        return false;
    });

    // big cell is the name swap cell
    $('.clickable_big_cell').bind('click', function() {
        var first_name_cell = this.children[0];
        var last_name_cell = this.children[2];
        if(first_name_cell.getAttribute("mode") != "full") {
            get_cell_ajax(first_name_cell);
        }
        if(last_name_cell.getAttribute("mode") != "full") {
            get_cell_ajax(last_name_cell);
        }
        this.classList.remove("clickable_big_cell");

        // save the user click data
        $this_click = "user click: " + first_name_cell.children[0].id + "user click: " + last_name_cell.children[0].id;
        var dt = new Date();
        $click_time = "click time: " + dt.getHours() + "h" + dt.getMinutes() + "m" + dt.getSeconds() + "s";
        $click_timestamp = "click timestamp: " + dt.getTime();
        $data = [$this_click, $click_time, $click_timestamp].join()
        $user_data += $data + ";";
        
        return false;
    });
}

// mirror: this function has a mirror at form_submit.js
function refresh_delta() {
    $('.clickable_cell').hover(function() {
        var id1 = this.children[0].getAttribute("id");
        var d = $DELTA[id1];
        var bar_style = 'width:' + d + '%';
        $("#privacy-risk-delta").attr("style", bar_style);
        $("#privacy-risk-delta-value").html(" + " + d + "%");

        var cd = $DELTA_CDP[id1]
        var bar_style_cdp = 'width:' + cd + '%';
        $("#character-percentage-delta").attr("style", bar_style_cdp);
        $("#character-percentage-delta-value").html(" + " + cd + "%");
    }, function() {
        $("#privacy-risk-delta").attr("style", 'width: 0%');
        $("#privacy-risk-delta-value").html(" ")
        $("#character-percentage-delta").attr("style", 'width: 0%');
        $("#character-percentage-delta-value").html(" ");
    });
}

$(function() {
    make_cell_clickable();
    refresh_delta();
});

